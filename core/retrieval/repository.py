from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import Float, exists, func, literal_column, or_, select
from sqlalchemy.orm import Session

from core.retrieval.embeddings import (
    EmbeddingSpaceDefinition,
    cosine_similarity,
    lexical_overlap_score,
)
from core.retrieval.models import GenerationResult, RetrievedEvidence
from core.security.access import AccessContext
from core.security.data_policy import (
    ModelDataPolicyError,
    allowed_sensitivities_for_boundary,
    require_boundary_allowed,
)
from core.storage.database import Database
from core.storage.models import (
    AnswerCitationRecord,
    AuditEventRecord,
    ConversationMessageRecord,
    ConversationRecord,
    DocumentChunkRecord,
    EmbeddingSpaceRecord,
    PersonaMemoryEmbeddingRecord,
    PersonaMemoryEvidenceRecord,
    PersonaMemoryRecord,
    PersonaMemoryVersionRecord,
    PersonaModelCallRecord,
    PersonaRecord,
    RetrievalRunRecord,
    SourceDocumentRecord,
    utc_now,
)


def _new_id() -> str:
    return str(uuid4())


def _record_dict(record: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in record.__table__.columns:
        value = getattr(record, column.name)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            value = value.isoformat()
        result[column.name] = value
    return result


def canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class PersonaRetrievalRepository:
    """Storage boundary that applies ownership and memory-state hard filters."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_embedding_space(
        self,
        definition: EmbeddingSpaceDefinition,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            existing = session.get(EmbeddingSpaceRecord, definition.id)
            expected = {
                "provider": definition.provider,
                "model_name": definition.model_name,
                "model_version": definition.model_version,
                "dimensions": definition.dimensions,
                "distance_metric": definition.distance_metric,
                "normalization": definition.normalization,
                "document_template_version": definition.document_template_version,
                "query_template_version": definition.query_template_version,
                "config_hash": definition.config_hash,
                "data_boundary": definition.data_boundary,
            }
            if existing is not None:
                actual = {key: getattr(existing, key) for key in expected}
                if actual != expected:
                    raise ValueError(
                        f"Embedding space {definition.id} metadata does not match"
                    )
                if existing.status == "retired":
                    existing.status = "active"
                    existing.activated_at = utc_now()
                    existing.retired_at = None
                return _record_dict(existing)
            record = EmbeddingSpaceRecord(
                id=definition.id,
                **expected,
                status="active",
            )
            session.add(record)
            session.flush()
            return _record_dict(record)

    def get_embedding_space(self, space_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            record = session.get(EmbeddingSpaceRecord, space_id)
            if record is None:
                raise KeyError(f"EmbeddingSpaceRecord not found: {space_id}")
            return _record_dict(record)

    def get_persona_policy(
        self,
        access: AccessContext,
        *,
        persona_id: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            persona = self._owned_persona(
                session,
                access,
                persona_id,
                require_active=True,
            )
            return {
                "persona_id": persona.id,
                "allowed_model_boundaries": list(
                    persona.allowed_model_boundaries or ["local"]
                ),
            }

    def get_conversation_context(
        self,
        access: AccessContext,
        *,
        conversation_id: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            conversation = self._owned_conversation(
                session,
                access,
                conversation_id,
                require_active=True,
            )
            persona = self._owned_persona(
                session,
                access,
                conversation.persona_id,
                require_active=True,
            )
            return {
                "conversation_id": conversation.id,
                "persona_id": persona.id,
                "allowed_model_boundaries": list(
                    persona.allowed_model_boundaries or ["local"]
                ),
            }

    def get_indexable_memory(
        self,
        access: AccessContext,
        memory_id: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            memory = session.get(PersonaMemoryRecord, memory_id)
            if (
                memory is None
                or memory.owner_id != access.owner_id
                or memory.status != "confirmed"
                or memory.visibility != "owner"
                or memory.current_version_id is None
            ):
                raise KeyError(f"Confirmed PersonaMemoryRecord not found: {memory_id}")
            persona = session.get(PersonaRecord, memory.persona_id)
            if persona is None or persona.status != "active":
                raise ValueError(f"Persona {memory.persona_id} is not active")
            version = session.get(
                PersonaMemoryVersionRecord,
                memory.current_version_id,
            )
            if version is None or version.memory_id != memory.id:
                raise ValueError(f"Memory {memory.id} has an invalid current version")
            self._require_live_evidence(session, version.id)
            return {
                "memory_id": memory.id,
                "memory_version_id": version.id,
                "persona_id": memory.persona_id,
                "owner_id": memory.owner_id,
                "sensitivity": memory.sensitivity,
                "content": (
                    f"{version.structured_summary}\n{version.raw_content}".strip()
                ),
                "content_sha256": canonical_hash(
                    {
                        "structured_summary": version.structured_summary,
                        "raw_content": version.raw_content,
                    }
                ),
            }

    def list_indexable_memories(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        allowed_sensitivities: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            self._owned_persona(session, access, persona_id, require_active=True)
            rows = list(
                session.execute(
                    select(PersonaMemoryRecord, PersonaMemoryVersionRecord)
                    .join(
                        PersonaMemoryVersionRecord,
                        PersonaMemoryVersionRecord.id
                        == PersonaMemoryRecord.current_version_id,
                    )
                    .where(
                        PersonaMemoryRecord.owner_id == access.owner_id,
                        PersonaMemoryRecord.persona_id == persona_id,
                        PersonaMemoryRecord.status == "confirmed",
                        PersonaMemoryRecord.visibility == "owner",
                        *(
                            (
                                PersonaMemoryRecord.sensitivity.in_(
                                    allowed_sensitivities
                                ),
                            )
                            if allowed_sensitivities is not None
                            else ()
                        ),
                        self._live_evidence_exists(PersonaMemoryVersionRecord.id),
                    )
                    .order_by(
                        PersonaMemoryRecord.confirmed_at,
                        PersonaMemoryRecord.id,
                    )
                )
            )
            return [
                {
                    "memory_id": memory.id,
                    "memory_version_id": version.id,
                    "persona_id": memory.persona_id,
                    "owner_id": memory.owner_id,
                    "sensitivity": memory.sensitivity,
                    "content": (
                        f"{version.structured_summary}\n{version.raw_content}".strip()
                    ),
                    "content_sha256": canonical_hash(
                        {
                            "structured_summary": version.structured_summary,
                            "raw_content": version.raw_content,
                        }
                    ),
                }
                for memory, version in rows
            ]

    def list_missing_indexable_memories(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        embedding_space_id: str,
        allowed_sensitivities: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        indexable = self.list_indexable_memories(
            access,
            persona_id=persona_id,
            allowed_sensitivities=allowed_sensitivities,
        )
        if not indexable:
            return []
        version_ids = [str(item["memory_version_id"]) for item in indexable]
        with self.database.session() as session:
            existing = set(
                session.scalars(
                    select(PersonaMemoryEmbeddingRecord.memory_version_id).where(
                        PersonaMemoryEmbeddingRecord.owner_id == access.owner_id,
                        PersonaMemoryEmbeddingRecord.persona_id == persona_id,
                        PersonaMemoryEmbeddingRecord.embedding_space_id
                        == embedding_space_id,
                        PersonaMemoryEmbeddingRecord.memory_version_id.in_(
                            version_ids
                        ),
                    )
                )
            )
        return [
            item
            for item in indexable
            if str(item["memory_version_id"]) not in existing
        ]

    def existing_memory_embedding(
        self,
        access: AccessContext,
        *,
        indexable: dict[str, Any],
        embedding_space_id: str,
    ) -> dict[str, Any] | None:
        with self.database.session() as session:
            record = session.scalar(
                select(PersonaMemoryEmbeddingRecord).where(
                    PersonaMemoryEmbeddingRecord.owner_id == access.owner_id,
                    PersonaMemoryEmbeddingRecord.persona_id
                    == str(indexable["persona_id"]),
                    PersonaMemoryEmbeddingRecord.memory_version_id
                    == str(indexable["memory_version_id"]),
                    PersonaMemoryEmbeddingRecord.embedding_space_id
                    == embedding_space_id,
                )
            )
            if record is None:
                return None
            if record.content_sha256 != str(indexable["content_sha256"]):
                raise ValueError(
                    "existing embedding content hash does not match memory version"
                )
            return _record_dict(record)

    def upsert_memory_embedding(
        self,
        access: AccessContext,
        *,
        indexable: dict[str, Any],
        space: EmbeddingSpaceDefinition,
        embedding: list[float],
    ) -> dict[str, Any]:
        if len(embedding) != space.dimensions:
            raise ValueError(
                f"Embedding has {len(embedding)} dimensions; "
                f"space {space.id} requires {space.dimensions}"
            )
        with self.database.session() as session:
            memory = session.get(
                PersonaMemoryRecord,
                str(indexable["memory_id"]),
            )
            if (
                memory is None
                or memory.owner_id != access.owner_id
                or memory.status != "confirmed"
                or memory.current_version_id != str(indexable["memory_version_id"])
            ):
                raise ValueError("memory changed before its embedding was written")
            persona = self._owned_persona(
                session,
                access,
                memory.persona_id,
                require_active=True,
            )
            require_boundary_allowed(
                allowed_boundaries=list(
                    persona.allowed_model_boundaries or ["local"]
                ),
                requested_boundary=space.data_boundary,
            )
            if memory.sensitivity not in allowed_sensitivities_for_boundary(
                space.data_boundary
            ):
                raise ModelDataPolicyError(
                    "Memory sensitivity is not allowed for the embedding "
                    f"data boundary: {space.data_boundary}"
                )
            existing = session.scalar(
                select(PersonaMemoryEmbeddingRecord).where(
                    PersonaMemoryEmbeddingRecord.memory_version_id
                    == str(indexable["memory_version_id"]),
                    PersonaMemoryEmbeddingRecord.embedding_space_id == space.id,
                )
            )
            if existing is not None:
                if existing.content_sha256 != str(indexable["content_sha256"]):
                    raise ValueError(
                        "existing embedding content hash does not match memory version"
                    )
                return {
                    "embedding": _record_dict(existing),
                    "created": False,
                }
            record = PersonaMemoryEmbeddingRecord(
                id=_new_id(),
                memory_id=memory.id,
                memory_version_id=str(indexable["memory_version_id"]),
                persona_id=memory.persona_id,
                owner_id=memory.owner_id,
                embedding_space_id=space.id,
                embedding=embedding,
                content_sha256=str(indexable["content_sha256"]),
            )
            session.add(record)
            session.flush()
            return {
                "embedding": _record_dict(record),
                "created": True,
            }

    def rank_lexical(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        query: str,
        limit: int,
        minimum_score: float,
        allowed_sensitivities: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            self._owned_persona(session, access, persona_id, require_active=True)
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                searchable = func.concat_ws(
                    " ",
                    PersonaMemoryVersionRecord.structured_summary,
                    PersonaMemoryVersionRecord.raw_content,
                )
                simple = literal_column("'simple'::regconfig")
                ts_vector = func.to_tsvector(simple, searchable)
                ts_query = func.websearch_to_tsquery(simple, query)
                text_rank = func.ts_rank_cd(ts_vector, ts_query)
                trigram_rank = func.similarity(searchable, query)
                combined = (text_rank + trigram_rank).label("score")
                rows = list(
                    session.execute(
                        select(
                            PersonaMemoryVersionRecord.id,
                            combined,
                        )
                        .join(
                            PersonaMemoryRecord,
                            PersonaMemoryRecord.current_version_id
                            == PersonaMemoryVersionRecord.id,
                        )
                        .where(
                            *self._memory_filters(
                                access,
                                persona_id,
                                allowed_sensitivities=allowed_sensitivities,
                            ),
                            self._live_evidence_exists(PersonaMemoryVersionRecord.id),
                            or_(
                                ts_vector.op("@@")(ts_query),
                                trigram_rank >= minimum_score,
                            ),
                        )
                        .order_by(combined.desc(), PersonaMemoryVersionRecord.id)
                        .limit(limit)
                    )
                )
                return [
                    {
                        "memory_version_id": version_id,
                        "score": float(score),
                    }
                    for version_id, score in rows
                    if float(score) >= minimum_score
                ]

            rows = list(
                session.execute(
                    select(PersonaMemoryVersionRecord)
                    .join(
                        PersonaMemoryRecord,
                        PersonaMemoryRecord.current_version_id
                        == PersonaMemoryVersionRecord.id,
                    )
                    .where(
                        *self._memory_filters(
                            access,
                            persona_id,
                            allowed_sensitivities=allowed_sensitivities,
                        ),
                        self._live_evidence_exists(PersonaMemoryVersionRecord.id),
                    )
                ).scalars()
            )
            ranked = [
                {
                    "memory_version_id": version.id,
                    "score": lexical_overlap_score(
                        query,
                        (f"{version.structured_summary} {version.raw_content}"),
                    ),
                }
                for version in rows
            ]
            return [
                item
                for item in sorted(
                    ranked,
                    key=lambda item: (
                        -float(item["score"]),
                        str(item["memory_version_id"]),
                    ),
                )[:limit]
                if float(item["score"]) >= minimum_score
            ]

    def rank_vector(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        embedding_space_id: str,
        query_embedding: list[float],
        limit: int,
        minimum_similarity: float,
        allowed_sensitivities: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            self._owned_persona(session, access, persona_id, require_active=True)
            space = session.get(EmbeddingSpaceRecord, embedding_space_id)
            if space is None or space.status != "active":
                raise KeyError(
                    f"Active EmbeddingSpaceRecord not found: {embedding_space_id}"
                )
            if len(query_embedding) != space.dimensions:
                raise ValueError("query vector does not match embedding space")
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                distance = PersonaMemoryEmbeddingRecord.embedding.op(
                    "<=>",
                    return_type=Float,
                )(query_embedding).label("distance")
                rows = list(
                    session.execute(
                        select(
                            PersonaMemoryEmbeddingRecord.memory_version_id,
                            distance,
                        )
                        .join(
                            PersonaMemoryRecord,
                            PersonaMemoryRecord.id
                            == PersonaMemoryEmbeddingRecord.memory_id,
                        )
                        .join(
                            PersonaMemoryVersionRecord,
                            PersonaMemoryVersionRecord.id
                            == PersonaMemoryEmbeddingRecord.memory_version_id,
                        )
                        .where(
                            *self._memory_filters(
                                access,
                                persona_id,
                                allowed_sensitivities=allowed_sensitivities,
                            ),
                            PersonaMemoryEmbeddingRecord.owner_id == access.owner_id,
                            PersonaMemoryEmbeddingRecord.persona_id == persona_id,
                            PersonaMemoryEmbeddingRecord.embedding_space_id
                            == embedding_space_id,
                            self._live_evidence_exists(PersonaMemoryVersionRecord.id),
                        )
                        .order_by(distance, PersonaMemoryEmbeddingRecord.id)
                        .limit(limit)
                    )
                )
                return [
                    {
                        "memory_version_id": version_id,
                        "similarity": 1.0 - float(distance_value),
                    }
                    for version_id, distance_value in rows
                    if 1.0 - float(distance_value) >= minimum_similarity
                ]

            rows = list(
                session.execute(
                    select(PersonaMemoryEmbeddingRecord)
                    .join(
                        PersonaMemoryRecord,
                        PersonaMemoryRecord.id
                        == PersonaMemoryEmbeddingRecord.memory_id,
                    )
                    .join(
                        PersonaMemoryVersionRecord,
                        PersonaMemoryVersionRecord.id
                        == PersonaMemoryEmbeddingRecord.memory_version_id,
                    )
                    .where(
                        *self._memory_filters(
                            access,
                            persona_id,
                            allowed_sensitivities=allowed_sensitivities,
                        ),
                        PersonaMemoryEmbeddingRecord.owner_id == access.owner_id,
                        PersonaMemoryEmbeddingRecord.persona_id == persona_id,
                        PersonaMemoryEmbeddingRecord.embedding_space_id
                        == embedding_space_id,
                        self._live_evidence_exists(PersonaMemoryVersionRecord.id),
                    )
                ).scalars()
            )
            ranked = [
                {
                    "memory_version_id": item.memory_version_id,
                    "similarity": cosine_similarity(
                        list(item.embedding),
                        query_embedding,
                    ),
                }
                for item in rows
            ]
            return [
                item
                for item in sorted(
                    ranked,
                    key=lambda item: (
                        -float(item["similarity"]),
                        str(item["memory_version_id"]),
                    ),
                )[:limit]
                if float(item["similarity"]) >= minimum_similarity
            ]

    def evidence_for_versions(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        version_ids: list[str],
        allowed_sensitivities: tuple[str, ...] | None = None,
    ) -> dict[str, dict[str, Any]]:
        if not version_ids:
            return {}
        with self.database.session() as session:
            self._owned_persona(session, access, persona_id, require_active=True)
            output: dict[str, dict[str, Any]] = {}
            for version_id in version_ids:
                row = session.execute(
                    select(
                        PersonaMemoryRecord,
                        PersonaMemoryVersionRecord,
                        PersonaMemoryEvidenceRecord,
                        SourceDocumentRecord,
                        DocumentChunkRecord,
                    )
                    .join(
                        PersonaMemoryVersionRecord,
                        PersonaMemoryVersionRecord.id
                        == PersonaMemoryRecord.current_version_id,
                    )
                    .join(
                        PersonaMemoryEvidenceRecord,
                        PersonaMemoryEvidenceRecord.memory_version_id
                        == PersonaMemoryVersionRecord.id,
                    )
                    .join(
                        SourceDocumentRecord,
                        SourceDocumentRecord.id
                        == PersonaMemoryEvidenceRecord.source_document_id,
                    )
                    .join(
                        DocumentChunkRecord,
                        DocumentChunkRecord.id
                        == PersonaMemoryEvidenceRecord.document_chunk_id,
                    )
                    .where(
                        *self._memory_filters(
                            access,
                            persona_id,
                            allowed_sensitivities=allowed_sensitivities,
                        ),
                        PersonaMemoryVersionRecord.id == version_id,
                        SourceDocumentRecord.status == "ready",
                    )
                    .order_by(
                        PersonaMemoryEvidenceRecord.created_at,
                        PersonaMemoryEvidenceRecord.id,
                    )
                    .limit(1)
                ).first()
                if row is None:
                    continue
                memory, version, evidence, source, chunk = row
                output[version.id] = {
                    "memory_id": memory.id,
                    "memory_version_id": version.id,
                    "evidence_id": evidence.id,
                    "evidence_relation": evidence.relation,
                    "source_document_id": source.id,
                    "document_chunk_id": chunk.id,
                    "memory_type": memory.memory_type,
                    "epistemic_status": memory.epistemic_status,
                    "sensitivity": memory.sensitivity,
                    "summary": version.structured_summary,
                    "excerpt": evidence.excerpt,
                    "locator": evidence.locator_snapshot,
                    "source": {
                        "id": source.id,
                        "filename": source.original_filename,
                        "media_type": source.media_type,
                        "content_sha256": source.content_sha256,
                        "chunk_ordinal": chunk.ordinal,
                    },
                }
            return output

    def create_conversation(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        title: str | None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            persona = self._owned_persona(
                session,
                access,
                persona_id,
                require_active=True,
            )
            record = ConversationRecord(
                id=_new_id(),
                persona_id=persona.id,
                owner_id=access.owner_id,
                title=title,
                status="active",
            )
            session.add(record)
            self._add_audit(
                session,
                access=access,
                persona_id=persona.id,
                action="conversation.created",
                resource_type="persona_conversation",
                resource_id=record.id,
                dedupe_key=f"conversation.created:{record.id}",
                detail={"title_provided": title is not None},
            )
            session.flush()
            return _record_dict(record)

    def list_messages(
        self,
        access: AccessContext,
        *,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            conversation = self._owned_conversation(
                session,
                access,
                conversation_id,
            )
            records = list(
                session.scalars(
                    select(ConversationMessageRecord)
                    .where(
                        ConversationMessageRecord.conversation_id == conversation.id,
                        ConversationMessageRecord.owner_id == access.owner_id,
                    )
                    .order_by(
                        ConversationMessageRecord.created_at,
                        ConversationMessageRecord.id,
                    )
                )
            )
            return [_record_dict(item) for item in records]

    def create_user_message(
        self,
        access: AccessContext,
        *,
        conversation_id: str,
        content: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            conversation = self._owned_conversation(
                session,
                access,
                conversation_id,
                require_active=True,
            )
            record = ConversationMessageRecord(
                id=_new_id(),
                conversation_id=conversation.id,
                persona_id=conversation.persona_id,
                owner_id=access.owner_id,
                role="user",
                content=content,
                answer_status="not_applicable",
            )
            conversation.updated_at = utc_now()
            session.add(record)
            session.flush()
            return _record_dict(record)

    def create_retrieval_run(
        self,
        access: AccessContext,
        *,
        conversation_id: str,
        user_message_id: str,
        embedding_space_id: str,
        query_sha256: str,
        candidates: list[dict[str, Any]],
        top_k: int,
        model_data_boundary: str,
        query_embedding_data_boundary: str,
        allowed_sensitivities: tuple[str, ...],
        model_evidence_projection: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            conversation = self._owned_conversation(
                session,
                access,
                conversation_id,
                require_active=True,
            )
            user_message = session.get(
                ConversationMessageRecord,
                user_message_id,
            )
            if (
                user_message is None
                or user_message.owner_id != access.owner_id
                or user_message.conversation_id != conversation.id
                or user_message.role != "user"
            ):
                raise KeyError(
                    f"ConversationMessageRecord not found: {user_message_id}"
                )
            record = RetrievalRunRecord(
                id=_new_id(),
                conversation_id=conversation.id,
                user_message_id=user_message.id,
                persona_id=conversation.persona_id,
                owner_id=access.owner_id,
                embedding_space_id=embedding_space_id,
                query_sha256=query_sha256,
                filters={
                    "owner_id": access.owner_id,
                    "persona_id": conversation.persona_id,
                    "memory_status": "confirmed",
                    "visibility": "owner",
                    "current_version_only": True,
                    "source_status": "ready",
                    "embedding_space_id": embedding_space_id,
                    "model_data_boundary": model_data_boundary,
                    "query_embedding_data_boundary": (
                        query_embedding_data_boundary
                    ),
                    "allowed_sensitivities": list(allowed_sensitivities),
                    "model_evidence_projection": model_evidence_projection,
                },
                candidates=candidates,
                top_k=top_k,
                status="completed" if candidates else "no_evidence",
            )
            session.add(record)
            session.flush()
            return _record_dict(record)

    def persist_answer(
        self,
        access: AccessContext,
        *,
        conversation_id: str,
        retrieval_run_id: str,
        generation: GenerationResult,
        evidence: list[RetrievedEvidence],
        claim_indexes: dict[str, list[int]],
        request_hash: str,
        latency_ms: int,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            conversation = self._owned_conversation(
                session,
                access,
                conversation_id,
                require_active=True,
            )
            retrieval = self._owned_retrieval(
                session,
                access,
                retrieval_run_id,
            )
            if retrieval.conversation_id != conversation.id:
                raise ValueError("retrieval run belongs to another conversation")
            assistant = ConversationMessageRecord(
                id=_new_id(),
                conversation_id=conversation.id,
                persona_id=conversation.persona_id,
                owner_id=access.owner_id,
                role="assistant",
                content=generation.draft.answer,
                answer_status="answered",
                claims=[claim.model_dump() for claim in generation.draft.claims],
                uncertainty=generation.draft.uncertainty,
                simulation_notice=self._persona_notice(
                    session, conversation.persona_id
                ),
            )
            session.add(assistant)
            session.flush()
            model_call = PersonaModelCallRecord(
                id=_new_id(),
                retrieval_run_id=retrieval.id,
                assistant_message_id=assistant.id,
                provider=generation.provider,
                model_name=generation.model_name,
                model_version=generation.model_version,
                prompt_template_version=generation.prompt_template_version,
                data_boundary=generation.data_boundary,
                request_hash=request_hash,
                response_hash=canonical_hash(generation.draft.model_dump()),
                usage=generation.usage,
                latency_ms=max(0, latency_ms),
                status="completed",
            )
            session.add(model_call)
            citation_records = []
            evidence_by_id = {item.citation_id: item for item in evidence}
            for citation_id, indexes in claim_indexes.items():
                item = evidence_by_id[citation_id]
                self._validate_evidence_for_answer(
                    session,
                    access=access,
                    persona_id=conversation.persona_id,
                    item=item,
                )
                citation = AnswerCitationRecord(
                    id=_new_id(),
                    assistant_message_id=assistant.id,
                    retrieval_run_id=retrieval.id,
                    citation_id=citation_id,
                    claim_indexes=indexes,
                    memory_id=item.memory_id,
                    memory_version_id=item.memory_version_id,
                    evidence_id=item.evidence_id,
                    source_document_id=item.source_document_id,
                    document_chunk_id=item.document_chunk_id,
                    locator_snapshot=item.locator,
                    excerpt=item.excerpt,
                    rank=item.rank,
                )
                session.add(citation)
                citation_records.append(citation)
            conversation.updated_at = utc_now()
            self._add_audit(
                session,
                access=access,
                persona_id=conversation.persona_id,
                action="question.answered",
                resource_type="persona_conversation_message",
                resource_id=assistant.id,
                dedupe_key=f"question.answered:{assistant.id}",
                detail={
                    "conversation_id": conversation.id,
                    "retrieval_run_id": retrieval.id,
                    "citation_count": len(citation_records),
                    "model_call_id": model_call.id,
                    "embedding_space_id": retrieval.embedding_space_id,
                },
            )
            session.flush()
            return {
                "assistant_message": _record_dict(assistant),
                "model_call": _record_dict(model_call),
                "citations": [
                    self._citation_bundle(session, item) for item in citation_records
                ],
            }

    def persist_no_evidence_answer(
        self,
        access: AccessContext,
        *,
        conversation_id: str,
        retrieval_run_id: str,
        answer: str,
        request_hash: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            conversation = self._owned_conversation(
                session,
                access,
                conversation_id,
                require_active=True,
            )
            retrieval = self._owned_retrieval(
                session,
                access,
                retrieval_run_id,
            )
            if (
                retrieval.conversation_id != conversation.id
                or retrieval.status != "no_evidence"
            ):
                raise ValueError("retrieval run is not a no-evidence result")
            assistant = ConversationMessageRecord(
                id=_new_id(),
                conversation_id=conversation.id,
                persona_id=conversation.persona_id,
                owner_id=access.owner_id,
                role="assistant",
                content=answer,
                answer_status="no_memory",
                claims=[],
                uncertainty={
                    "level": "no_evidence",
                    "message": "没有召回可引用的已确认记忆，未调用回答模型。",
                },
                simulation_notice=self._persona_notice(
                    session, conversation.persona_id
                ),
            )
            session.add(assistant)
            session.flush()
            model_call = PersonaModelCallRecord(
                id=_new_id(),
                retrieval_run_id=retrieval.id,
                assistant_message_id=assistant.id,
                provider="personaos",
                model_name="no-evidence-boundary",
                model_version="1.0.0",
                prompt_template_version="persona-answer-v1",
                data_boundary="local",
                request_hash=request_hash,
                response_hash=canonical_hash({"answer": answer}),
                usage={},
                latency_ms=0,
                status="skipped",
            )
            session.add(model_call)
            conversation.updated_at = utc_now()
            self._add_audit(
                session,
                access=access,
                persona_id=conversation.persona_id,
                action="question.no_memory",
                resource_type="persona_conversation_message",
                resource_id=assistant.id,
                dedupe_key=f"question.no_memory:{assistant.id}",
                detail={
                    "conversation_id": conversation.id,
                    "retrieval_run_id": retrieval.id,
                    "embedding_space_id": retrieval.embedding_space_id,
                    "model_invoked": False,
                },
            )
            session.flush()
            return {
                "assistant_message": _record_dict(assistant),
                "model_call": _record_dict(model_call),
                "citations": [],
            }

    def get_message_citations(
        self,
        access: AccessContext,
        *,
        message_id: str,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            message = session.get(ConversationMessageRecord, message_id)
            if (
                message is None
                or message.owner_id != access.owner_id
                or message.role != "assistant"
            ):
                raise KeyError(
                    f"Assistant ConversationMessageRecord not found: {message_id}"
                )
            records = list(
                session.scalars(
                    select(AnswerCitationRecord)
                    .where(AnswerCitationRecord.assistant_message_id == message.id)
                    .order_by(
                        AnswerCitationRecord.rank,
                        AnswerCitationRecord.citation_id,
                    )
                )
            )
            return [self._citation_bundle(session, item) for item in records]

    def record_reindex_audit(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        embedding_space_id: str,
        indexed_count: int,
        created_count: int,
        task_id: str | None,
    ) -> None:
        with self.database.session() as session:
            self._owned_persona(session, access, persona_id)
            self._add_audit(
                session,
                access=access,
                persona_id=persona_id,
                action="memories.reindexed",
                resource_type="persona",
                resource_id=persona_id,
                dedupe_key=(
                    f"memories.reindexed:{persona_id}:{embedding_space_id}:"
                    f"{task_id or _new_id()}"
                ),
                detail={
                    "embedding_space_id": embedding_space_id,
                    "indexed_count": indexed_count,
                    "created_count": created_count,
                    "task_id": task_id,
                },
            )

    def record_memory_index_audit(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        memory_id: str,
        memory_version_id: str,
        embedding_space_id: str,
        created: bool,
    ) -> None:
        with self.database.session() as session:
            self._owned_persona(session, access, persona_id)
            self._add_audit(
                session,
                access=access,
                persona_id=persona_id,
                action="memory.indexed",
                resource_type="persona_memory",
                resource_id=memory_id,
                dedupe_key=(f"memory.indexed:{memory_version_id}:{embedding_space_id}"),
                detail={
                    "memory_version_id": memory_version_id,
                    "embedding_space_id": embedding_space_id,
                    "created": created,
                },
            )

    @staticmethod
    def _memory_filters(
        access: AccessContext,
        persona_id: str,
        *,
        allowed_sensitivities: tuple[str, ...] | None = None,
    ) -> tuple[Any, ...]:
        filters = (
            PersonaMemoryRecord.owner_id == access.owner_id,
            PersonaMemoryRecord.persona_id == persona_id,
            PersonaMemoryRecord.status == "confirmed",
            PersonaMemoryRecord.visibility == "owner",
            PersonaMemoryRecord.current_version_id == PersonaMemoryVersionRecord.id,
        )
        if allowed_sensitivities is None:
            return filters
        return (
            *filters,
            PersonaMemoryRecord.sensitivity.in_(allowed_sensitivities),
        )

    @staticmethod
    def _live_evidence_exists(version_id: Any) -> Any:
        return exists(
            select(PersonaMemoryEvidenceRecord.id)
            .join(
                SourceDocumentRecord,
                SourceDocumentRecord.id
                == PersonaMemoryEvidenceRecord.source_document_id,
            )
            .where(
                PersonaMemoryEvidenceRecord.memory_version_id == version_id,
                SourceDocumentRecord.status == "ready",
            )
        )

    def _require_live_evidence(self, session: Session, version_id: str) -> None:
        found = session.scalar(
            select(PersonaMemoryEvidenceRecord.id)
            .join(
                SourceDocumentRecord,
                SourceDocumentRecord.id
                == PersonaMemoryEvidenceRecord.source_document_id,
            )
            .where(
                PersonaMemoryEvidenceRecord.memory_version_id == version_id,
                SourceDocumentRecord.status == "ready",
            )
            .limit(1)
        )
        if found is None:
            raise ValueError(
                f"Memory version {version_id} has no available source evidence"
            )

    @staticmethod
    def _owned_persona(
        session: Session,
        access: AccessContext,
        persona_id: str,
        *,
        require_active: bool = False,
    ) -> PersonaRecord:
        persona = session.get(PersonaRecord, persona_id)
        if persona is None or persona.owner_id != access.owner_id:
            raise KeyError(f"PersonaRecord not found: {persona_id}")
        if require_active and persona.status != "active":
            raise ValueError(f"Persona {persona_id} is not active")
        return persona

    @staticmethod
    def _owned_conversation(
        session: Session,
        access: AccessContext,
        conversation_id: str,
        *,
        require_active: bool = False,
    ) -> ConversationRecord:
        conversation = session.get(ConversationRecord, conversation_id)
        if conversation is None or conversation.owner_id != access.owner_id:
            raise KeyError(f"ConversationRecord not found: {conversation_id}")
        if require_active and conversation.status != "active":
            raise ValueError(f"Conversation {conversation_id} is not active")
        return conversation

    @staticmethod
    def _owned_retrieval(
        session: Session,
        access: AccessContext,
        retrieval_id: str,
    ) -> RetrievalRunRecord:
        record = session.get(RetrievalRunRecord, retrieval_id)
        if record is None or record.owner_id != access.owner_id:
            raise KeyError(f"RetrievalRunRecord not found: {retrieval_id}")
        return record

    @staticmethod
    def _persona_notice(session: Session, persona_id: str) -> str:
        persona = session.get(PersonaRecord, persona_id)
        if persona is None:
            raise ValueError(f"Persona {persona_id} no longer exists")
        return persona.simulation_notice

    @staticmethod
    def _citation_bundle(
        session: Session,
        citation: AnswerCitationRecord,
    ) -> dict[str, Any]:
        source = session.get(SourceDocumentRecord, citation.source_document_id)
        chunk = session.get(DocumentChunkRecord, citation.document_chunk_id)
        memory = session.get(PersonaMemoryRecord, citation.memory_id)
        version = session.get(
            PersonaMemoryVersionRecord,
            citation.memory_version_id,
        )
        evidence = session.get(
            PersonaMemoryEvidenceRecord,
            citation.evidence_id,
        )
        if any(item is None for item in (source, chunk, memory, version, evidence)):
            raise ValueError(f"Citation {citation.id} has a missing source")
        return {
            "citation": _record_dict(citation),
            "memory": {
                "id": memory.id,
                "memory_type": memory.memory_type,
                "status": memory.status,
                "epistemic_status": memory.epistemic_status,
                "sensitivity": memory.sensitivity,
                "version": version.version,
                "structured_summary": version.structured_summary,
            },
            "evidence": {
                "id": evidence.id,
                "relation": evidence.relation,
                "excerpt_sha256": evidence.excerpt_sha256,
            },
            "source": {
                "id": source.id,
                "filename": source.original_filename,
                "media_type": source.media_type,
                "content_sha256": source.content_sha256,
                "locator": citation.locator_snapshot,
                "excerpt": citation.excerpt,
                "chunk_ordinal": chunk.ordinal,
            },
        }

    @staticmethod
    def _validate_evidence_for_answer(
        session: Session,
        *,
        access: AccessContext,
        persona_id: str,
        item: RetrievedEvidence,
    ) -> None:
        memory = session.get(PersonaMemoryRecord, item.memory_id)
        version = session.get(
            PersonaMemoryVersionRecord,
            item.memory_version_id,
        )
        evidence = session.get(
            PersonaMemoryEvidenceRecord,
            item.evidence_id,
        )
        source = session.get(SourceDocumentRecord, item.source_document_id)
        chunk = session.get(DocumentChunkRecord, item.document_chunk_id)
        valid = (
            memory is not None
            and memory.owner_id == access.owner_id
            and memory.persona_id == persona_id
            and memory.status == "confirmed"
            and memory.visibility == "owner"
            and memory.current_version_id == item.memory_version_id
            and version is not None
            and version.memory_id == item.memory_id
            and evidence is not None
            and evidence.memory_version_id == item.memory_version_id
            and evidence.source_document_id == item.source_document_id
            and evidence.document_chunk_id == item.document_chunk_id
            and source is not None
            and source.owner_id == access.owner_id
            and source.persona_id == persona_id
            and source.status == "ready"
            and chunk is not None
            and chunk.document_id == item.source_document_id
            and chunk.persona_id == persona_id
        )
        if not valid:
            raise ValueError(
                f"Citation {item.citation_id} is no longer current and authorized"
            )

    @staticmethod
    def _add_audit(
        session: Session,
        *,
        access: AccessContext,
        persona_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        dedupe_key: str,
        detail: dict[str, Any],
    ) -> None:
        if (
            session.scalar(
                select(AuditEventRecord.id).where(
                    AuditEventRecord.owner_id == access.owner_id,
                    AuditEventRecord.dedupe_key == dedupe_key,
                )
            )
            is not None
        ):
            return
        session.add(
            AuditEventRecord(
                id=_new_id(),
                request_id=access.request_id,
                correlation_id=access.correlation_id,
                dedupe_key=dedupe_key,
                actor_type=access.actor_type,
                actor_id=access.actor_id,
                owner_id=access.owner_id,
                persona_id=persona_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome="succeeded",
                risk_level="low",
                detail=detail,
            )
        )
