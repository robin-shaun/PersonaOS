from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from core.security.access import AccessContext
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
    PersonaMemoryRelationRecord,
    PersonaMemoryVersionRecord,
    PersonaModelCallRecord,
    PersonaRecord,
    RetrievalRunRecord,
    SourceDocumentRecord,
    utc_now,
)

DELETED_ANSWER_NOTICE = "此回答依赖的记忆或来源已被删除，原回答内容和引用已一并移除。"


def _new_id() -> str:
    return str(uuid4())


def _record_dict(record: Any) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for column in record.__table__.columns:
        value = getattr(record, column.name)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            value = value.isoformat()
        output[column.name] = value
    return output


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class PersonaLifecycleRepository:
    """Transactional persistence for mutable policy and destructive lifecycle work."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def update_model_policy(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        allowed_model_boundaries: list[str],
        external_data_acknowledged: bool,
    ) -> dict[str, Any]:
        with self.database.session(owner_id=access.owner_id) as session:
            persona = self._owned_persona_for_update(
                session,
                access,
                persona_id,
            )
            previous = list(persona.allowed_model_boundaries or ["local"])
            if "external" in allowed_model_boundaries and (
                "external" not in previous and not external_data_acknowledged
            ):
                raise ValueError(
                    "external_data_acknowledged must be true when enabling "
                    "the external model boundary"
                )
            if previous == allowed_model_boundaries:
                return _record_dict(persona)
            before_hash = _canonical_hash(previous)
            persona.allowed_model_boundaries = allowed_model_boundaries
            persona.version += 1
            persona.updated_at = utc_now()
            self._add_audit(
                session,
                access=access,
                persona_id=persona.id,
                action="persona.model_policy_updated",
                resource_type="persona",
                resource_id=persona.id,
                dedupe_key=f"persona.model_policy:{persona.id}:{persona.version}",
                risk_level="high"
                if "external" in allowed_model_boundaries
                else "medium",
                before_hash=before_hash,
                after_hash=_canonical_hash(allowed_model_boundaries),
                detail={
                    "previous_boundaries": previous,
                    "allowed_model_boundaries": allowed_model_boundaries,
                    "external_data_acknowledged": external_data_acknowledged,
                    "persona_version": persona.version,
                },
            )
            session.flush()
            return _record_dict(persona)

    def update_memory(
        self,
        access: AccessContext,
        *,
        memory_id: str,
        expected_version: int,
        content: str | None,
        sensitivity: str | None,
        reason: str,
    ) -> dict[str, Any]:
        with self.database.session(owner_id=access.owner_id) as session:
            memory = self._owned_memory_for_update(session, access, memory_id)
            if memory.status != "confirmed":
                raise ValueError(
                    f"Memory {memory_id} cannot be edited from status {memory.status}"
                )
            current = self._current_version(session, memory)
            if current.version != expected_version:
                raise ValueError(
                    f"Memory version conflict: expected {expected_version}, "
                    f"current {current.version}"
                )
            next_content = current.raw_content if content is None else content
            next_sensitivity = (
                memory.sensitivity if sensitivity is None else sensitivity
            )
            previous_sensitivity = memory.sensitivity
            content_changed = next_content != current.raw_content
            sensitivity_changed = next_sensitivity != memory.sensitivity
            if not content_changed and not sensitivity_changed:
                raise ValueError("Memory update does not change content or sensitivity")
            source_bound = (
                False
                if content_changed
                else bool(
                    (current.metadata_snapshot or {}).get(
                        "source_bound",
                        True,
                    )
                )
            )
            evidence_records = list(
                session.scalars(
                    select(PersonaMemoryEvidenceRecord).where(
                        PersonaMemoryEvidenceRecord.memory_version_id == current.id
                    )
                )
            )
            if not evidence_records:
                raise ValueError(f"Memory version {current.id} has no source evidence")
            source_ids = {item.source_document_id for item in evidence_records}
            sources = list(
                session.scalars(
                    select(SourceDocumentRecord)
                    .where(SourceDocumentRecord.id.in_(source_ids))
                    .with_for_update()
                )
            )
            if len(sources) != len(source_ids) or any(
                item.status != "ready" for item in sources
            ):
                raise ValueError(
                    "Memory cannot be edited while source evidence is unavailable"
                )

            next_version = PersonaMemoryVersionRecord(
                id=_new_id(),
                memory_id=memory.id,
                version=current.version + 1,
                raw_content=next_content,
                structured_summary=self._summary(next_content),
                metadata_snapshot={
                    **(current.metadata_snapshot or {}),
                    "user_confirmed": True,
                    "edited_from_version": current.version,
                    "content_edited_after_confirmation": content_changed,
                    "sensitivity": next_sensitivity,
                    "source_bound": source_bound,
                },
                created_by_type=access.actor_type,
                created_by_id=access.actor_id,
                change_reason=reason,
                extractor_name="human-edit",
                extractor_version="1.0.0",
            )
            session.add(next_version)
            session.flush()
            for evidence in evidence_records:
                session.add(
                    PersonaMemoryEvidenceRecord(
                        id=_new_id(),
                        memory_version_id=next_version.id,
                        source_document_id=evidence.source_document_id,
                        document_chunk_id=evidence.document_chunk_id,
                        relation=(
                            "derived_from" if content_changed else evidence.relation
                        ),
                        locator_snapshot=evidence.locator_snapshot,
                        excerpt=evidence.excerpt,
                        excerpt_sha256=evidence.excerpt_sha256,
                    )
                )
            before_hash = _canonical_hash(
                {
                    "memory_version_id": current.id,
                    "version": current.version,
                    "content_sha256": sha256(
                        current.raw_content.encode("utf-8")
                    ).hexdigest(),
                    "sensitivity": memory.sensitivity,
                }
            )
            if content_changed:
                memory.epistemic_status = "user_asserted"
            memory.sensitivity = next_sensitivity
            memory.current_version_id = next_version.id
            memory.updated_at = utc_now()
            after_hash = _canonical_hash(
                {
                    "memory_version_id": next_version.id,
                    "version": next_version.version,
                    "content_sha256": sha256(next_content.encode("utf-8")).hexdigest(),
                    "sensitivity": next_sensitivity,
                }
            )
            self._add_audit(
                session,
                access=access,
                persona_id=memory.persona_id,
                action="memory.updated",
                resource_type="persona_memory",
                resource_id=memory.id,
                dedupe_key=f"memory.updated:{memory.id}:{next_version.version}",
                risk_level="medium",
                before_hash=before_hash,
                after_hash=after_hash,
                detail={
                    "previous_version_id": current.id,
                    "new_version_id": next_version.id,
                    "previous_version": current.version,
                    "new_version": next_version.version,
                    "content_changed": content_changed,
                    "sensitivity_changed": sensitivity_changed,
                    "previous_sensitivity": (previous_sensitivity),
                    "new_sensitivity": next_sensitivity,
                    "source_bound": source_bound,
                    "reason_provided": bool(reason),
                },
            )
            session.flush()
            return {
                "persona_id": memory.persona_id,
                "memory_id": memory.id,
                "memory_version_id": next_version.id,
                "version": next_version.version,
            }

    def create_relation(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        from_memory_id: str,
        to_memory_id: str,
        relation: str,
        confidence: float,
        evidence_memory_version_ids: list[str],
    ) -> dict[str, Any]:
        if from_memory_id == to_memory_id:
            raise ValueError("A memory relation cannot reference itself")
        if relation not in {
            "supports",
            "conflicts",
            "derived_from",
            "supersedes",
            "related_to",
        }:
            raise ValueError(f"Unsupported memory relation: {relation}")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("Relation confidence must be between 0 and 1")
        with self.database.session(owner_id=access.owner_id) as session:
            self._owned_persona(session, access, persona_id)
            from_memory = self._owned_memory(session, access, from_memory_id)
            to_memory = self._owned_memory(session, access, to_memory_id)
            if (
                from_memory.persona_id != persona_id
                or to_memory.persona_id != persona_id
            ):
                raise ValueError("Both memories must belong to the requested persona")
            if from_memory.status != "confirmed" or to_memory.status != "confirmed":
                raise ValueError("Memory relations require two confirmed memories")
            self._validate_relation_evidence(
                session,
                memory_ids={from_memory.id, to_memory.id},
                version_ids=evidence_memory_version_ids,
            )
            existing = session.scalar(
                select(PersonaMemoryRelationRecord).where(
                    PersonaMemoryRelationRecord.from_memory_id == from_memory.id,
                    PersonaMemoryRelationRecord.to_memory_id == to_memory.id,
                    PersonaMemoryRelationRecord.relation == relation,
                )
            )
            if existing is not None:
                return {"relation": _record_dict(existing), "created": False}
            record = PersonaMemoryRelationRecord(
                id=_new_id(),
                owner_id=access.owner_id,
                persona_id=persona_id,
                from_memory_id=from_memory.id,
                to_memory_id=to_memory.id,
                relation=relation,
                confidence=confidence,
                evidence_memory_version_ids=evidence_memory_version_ids,
                created_by_type=access.actor_type,
                created_by_id=access.actor_id,
            )
            session.add(record)
            self._add_audit(
                session,
                access=access,
                persona_id=persona_id,
                action="memory_relation.created",
                resource_type="persona_memory_relation",
                resource_id=record.id,
                dedupe_key=f"memory_relation.created:{record.id}",
                detail={
                    "from_memory_id": from_memory.id,
                    "to_memory_id": to_memory.id,
                    "relation": relation,
                    "confidence": confidence,
                    "evidence_memory_version_ids": evidence_memory_version_ids,
                },
            )
            session.flush()
            return {"relation": _record_dict(record), "created": True}

    def list_relations(
        self,
        access: AccessContext,
        *,
        memory_id: str,
    ) -> list[dict[str, Any]]:
        with self.database.session(owner_id=access.owner_id) as session:
            memory = self._owned_memory(session, access, memory_id)
            records = list(
                session.scalars(
                    select(PersonaMemoryRelationRecord)
                    .where(
                        PersonaMemoryRelationRecord.owner_id == access.owner_id,
                        PersonaMemoryRelationRecord.persona_id == memory.persona_id,
                        or_(
                            PersonaMemoryRelationRecord.from_memory_id == memory.id,
                            PersonaMemoryRelationRecord.to_memory_id == memory.id,
                        ),
                    )
                    .order_by(
                        PersonaMemoryRelationRecord.created_at,
                        PersonaMemoryRelationRecord.id,
                    )
                )
            )
            return [_record_dict(item) for item in records]

    def delete_relation(
        self,
        access: AccessContext,
        *,
        relation_id: str,
    ) -> dict[str, Any]:
        with self.database.session(owner_id=access.owner_id) as session:
            receipt = self._deletion_receipt(
                session,
                access,
                action="memory_relation.deleted",
                resource_id=relation_id,
            )
            if receipt is not None:
                return receipt
            relation = session.get(PersonaMemoryRelationRecord, relation_id)
            if relation is None or relation.owner_id != access.owner_id:
                raise KeyError(f"PersonaMemoryRelationRecord not found: {relation_id}")
            persona_id = relation.persona_id
            detail = {
                "from_memory_id": relation.from_memory_id,
                "to_memory_id": relation.to_memory_id,
                "relation": relation.relation,
            }
            session.delete(relation)
            audit_id = self._add_audit(
                session,
                access=access,
                persona_id=persona_id,
                action="memory_relation.deleted",
                resource_type="persona_memory_relation",
                resource_id=relation_id,
                dedupe_key=f"memory_relation.deleted:{relation_id}",
                risk_level="high",
                detail=detail,
            )
            session.flush()
            return {
                "id": relation_id,
                "deleted": True,
                "idempotency_replayed": False,
                "audit_event_id": audit_id,
                **detail,
            }

    def delete_memory(
        self,
        access: AccessContext,
        *,
        memory_id: str,
    ) -> dict[str, Any]:
        with self.database.session(owner_id=access.owner_id) as session:
            receipt = self._deletion_receipt(
                session,
                access,
                action="memory.deleted",
                resource_id=memory_id,
            )
            if receipt is not None:
                return receipt
            memory = self._owned_memory_for_update(session, access, memory_id)
            version_ids = list(
                session.scalars(
                    select(PersonaMemoryVersionRecord.id).where(
                        PersonaMemoryVersionRecord.memory_id == memory.id
                    )
                )
            )
            redaction = self._redact_dependent_answers(
                session,
                owner_id=access.owner_id,
                persona_id=memory.persona_id,
                memory_ids={memory.id},
                memory_version_ids=set(version_ids),
                source_document_id=None,
            )
            relation_count = self._delete_relations_for_memories(
                session,
                {memory.id},
            )
            embedding_count = self._delete_count(
                session,
                PersonaMemoryEmbeddingRecord,
                PersonaMemoryEmbeddingRecord.memory_id.in_({memory.id}),
            )
            evidence_count = self._delete_count(
                session,
                PersonaMemoryEvidenceRecord,
                PersonaMemoryEvidenceRecord.memory_version_id.in_(version_ids),
            )
            version_count = self._delete_count(
                session,
                PersonaMemoryVersionRecord,
                PersonaMemoryVersionRecord.memory_id == memory.id,
            )
            persona_id = memory.persona_id
            session.delete(memory)
            detail = {
                "version_count": version_count,
                "evidence_count": evidence_count,
                "embedding_count": embedding_count,
                "relation_count": relation_count,
                **redaction,
            }
            audit_id = self._add_audit(
                session,
                access=access,
                persona_id=persona_id,
                action="memory.deleted",
                resource_type="persona_memory",
                resource_id=memory_id,
                dedupe_key=f"memory.deleted:{memory_id}",
                risk_level="high",
                detail=detail,
            )
            session.flush()
            return {
                "id": memory_id,
                "deleted": True,
                "idempotency_replayed": False,
                "audit_event_id": audit_id,
                **detail,
            }

    def get_document_deletion_receipt(
        self,
        access: AccessContext,
        *,
        document_id: str,
    ) -> dict[str, Any] | None:
        with self.database.session(owner_id=access.owner_id) as session:
            return self._deletion_receipt(
                session,
                access,
                action="document.deleted",
                resource_id=document_id,
            )

    def prepare_document_deletion(
        self,
        access: AccessContext,
        *,
        document_id: str,
        ingestion_task_settled: bool = False,
    ) -> dict[str, Any]:
        # Blob object keys are content-addressed across accounts. This system
        # transaction performs one global reference count after the explicit
        # owner check so deleting one account cannot remove another's blob.
        with self.database.session(system=True) as session:
            document = self._owned_document_for_update(
                session,
                access,
                document_id,
            )
            if document.status == "deleted":
                raise ValueError(f"Document {document_id} is already deleted")
            if document.status == "processing" and not ingestion_task_settled:
                raise ValueError(
                    f"Document {document_id} is still processing; "
                    "cancel its ingestion task and retry"
                )
            if document.status != "deleting":
                document.status = "deleting"
                document.updated_at = utc_now()
            other_references = list(
                session.scalars(
                    select(SourceDocumentRecord.id).where(
                        SourceDocumentRecord.object_key == document.object_key,
                        SourceDocumentRecord.id != document.id,
                        SourceDocumentRecord.status != "deleted",
                    )
                )
            )
            session.flush()
            return {
                "document_id": document.id,
                "persona_id": document.persona_id,
                "object_key": document.object_key,
                "content_sha256": document.content_sha256,
                "task_id": document.task_id,
                "other_blob_reference_count": len(other_references),
            }

    def purge_document(
        self,
        access: AccessContext,
        *,
        document_id: str,
        blob_deleted: bool,
        blob_shared: bool,
    ) -> dict[str, Any]:
        with self.database.session(owner_id=access.owner_id) as session:
            receipt = self._deletion_receipt(
                session,
                access,
                action="document.deleted",
                resource_id=document_id,
            )
            if receipt is not None:
                return receipt
            document = self._owned_document_for_update(
                session,
                access,
                document_id,
            )
            if document.status != "deleting":
                raise ValueError(
                    f"Document {document_id} must be prepared before deletion"
                )
            primary_ids = set(
                session.scalars(
                    select(PersonaMemoryRecord.id).where(
                        PersonaMemoryRecord.source_document_id == document.id
                    )
                )
            )
            evidence_ids = set(
                session.scalars(
                    select(PersonaMemoryVersionRecord.memory_id)
                    .join(
                        PersonaMemoryEvidenceRecord,
                        PersonaMemoryEvidenceRecord.memory_version_id
                        == PersonaMemoryVersionRecord.id,
                    )
                    .where(
                        PersonaMemoryEvidenceRecord.source_document_id == document.id
                    )
                )
            )
            memory_ids = primary_ids | evidence_ids
            version_ids = (
                set(
                    session.scalars(
                        select(PersonaMemoryVersionRecord.id).where(
                            PersonaMemoryVersionRecord.memory_id.in_(memory_ids)
                        )
                    )
                )
                if memory_ids
                else set()
            )
            redaction = self._redact_dependent_answers(
                session,
                owner_id=access.owner_id,
                persona_id=document.persona_id,
                memory_ids=memory_ids,
                memory_version_ids=version_ids,
                source_document_id=document.id,
            )
            relation_count = self._delete_relations_for_memories(
                session,
                memory_ids,
            )
            embedding_count = (
                self._delete_count(
                    session,
                    PersonaMemoryEmbeddingRecord,
                    PersonaMemoryEmbeddingRecord.memory_id.in_(memory_ids),
                )
                if memory_ids
                else 0
            )
            evidence_count = (
                self._delete_count(
                    session,
                    PersonaMemoryEvidenceRecord,
                    or_(
                        PersonaMemoryEvidenceRecord.memory_version_id.in_(version_ids),
                        PersonaMemoryEvidenceRecord.source_document_id == document.id,
                    ),
                )
                if version_ids
                else self._delete_count(
                    session,
                    PersonaMemoryEvidenceRecord,
                    PersonaMemoryEvidenceRecord.source_document_id == document.id,
                )
            )
            version_count = (
                self._delete_count(
                    session,
                    PersonaMemoryVersionRecord,
                    PersonaMemoryVersionRecord.memory_id.in_(memory_ids),
                )
                if memory_ids
                else 0
            )
            memory_count = (
                self._delete_count(
                    session,
                    PersonaMemoryRecord,
                    PersonaMemoryRecord.id.in_(memory_ids),
                )
                if memory_ids
                else 0
            )
            chunk_count = self._delete_count(
                session,
                DocumentChunkRecord,
                DocumentChunkRecord.document_id == document.id,
            )
            persona_id = document.persona_id
            content_sha256 = document.content_sha256
            task_id = document.task_id
            session.delete(document)
            detail = {
                "content_sha256": content_sha256,
                "ingestion_task_id": task_id,
                "blob_deleted": blob_deleted,
                "blob_shared": blob_shared,
                "blob_absent": not blob_shared,
                "chunk_count": chunk_count,
                "memory_count": memory_count,
                "version_count": version_count,
                "evidence_count": evidence_count,
                "embedding_count": embedding_count,
                "relation_count": relation_count,
                **redaction,
            }
            audit_id = self._add_audit(
                session,
                access=access,
                persona_id=persona_id,
                action="document.deleted",
                resource_type="source_document",
                resource_id=document_id,
                dedupe_key=f"document.deleted:{document_id}",
                risk_level="high",
                before_hash=content_sha256,
                detail=detail,
            )
            session.flush()
            return {
                "id": document_id,
                "deleted": True,
                "idempotency_replayed": False,
                "audit_event_id": audit_id,
                **detail,
            }

    def export_snapshot(
        self,
        access: AccessContext,
        *,
        persona_id: str,
    ) -> dict[str, Any]:
        with self.database.session(owner_id=access.owner_id) as session:
            persona = self._owned_persona(session, access, persona_id)
            documents = list(
                session.scalars(
                    select(SourceDocumentRecord)
                    .where(
                        SourceDocumentRecord.persona_id == persona.id,
                        SourceDocumentRecord.owner_id == access.owner_id,
                    )
                    .order_by(SourceDocumentRecord.created_at, SourceDocumentRecord.id)
                )
            )
            document_ids = [item.id for item in documents]
            chunks = self._records(
                session,
                select(DocumentChunkRecord)
                .where(DocumentChunkRecord.document_id.in_(document_ids))
                .order_by(
                    DocumentChunkRecord.document_id,
                    DocumentChunkRecord.ordinal,
                ),
            )
            memories_raw = list(
                session.scalars(
                    select(PersonaMemoryRecord)
                    .where(
                        PersonaMemoryRecord.persona_id == persona.id,
                        PersonaMemoryRecord.owner_id == access.owner_id,
                    )
                    .order_by(PersonaMemoryRecord.created_at, PersonaMemoryRecord.id)
                )
            )
            memory_ids = [item.id for item in memories_raw]
            versions_raw = (
                list(
                    session.scalars(
                        select(PersonaMemoryVersionRecord)
                        .where(PersonaMemoryVersionRecord.memory_id.in_(memory_ids))
                        .order_by(
                            PersonaMemoryVersionRecord.memory_id,
                            PersonaMemoryVersionRecord.version,
                        )
                    )
                )
                if memory_ids
                else []
            )
            version_ids = [item.id for item in versions_raw]
            evidence = (
                self._records(
                    session,
                    select(PersonaMemoryEvidenceRecord)
                    .where(
                        PersonaMemoryEvidenceRecord.memory_version_id.in_(version_ids)
                    )
                    .order_by(
                        PersonaMemoryEvidenceRecord.memory_version_id,
                        PersonaMemoryEvidenceRecord.created_at,
                    ),
                )
                if version_ids
                else []
            )
            relations = self._records(
                session,
                select(PersonaMemoryRelationRecord)
                .where(
                    PersonaMemoryRelationRecord.persona_id == persona.id,
                    PersonaMemoryRelationRecord.owner_id == access.owner_id,
                )
                .order_by(
                    PersonaMemoryRelationRecord.created_at,
                    PersonaMemoryRelationRecord.id,
                ),
            )
            conversations_raw = list(
                session.scalars(
                    select(ConversationRecord)
                    .where(
                        ConversationRecord.persona_id == persona.id,
                        ConversationRecord.owner_id == access.owner_id,
                    )
                    .order_by(ConversationRecord.created_at, ConversationRecord.id)
                )
            )
            conversation_ids = [item.id for item in conversations_raw]
            messages = (
                self._records(
                    session,
                    select(ConversationMessageRecord)
                    .where(
                        ConversationMessageRecord.conversation_id.in_(conversation_ids)
                    )
                    .order_by(
                        ConversationMessageRecord.created_at,
                        ConversationMessageRecord.id,
                    ),
                )
                if conversation_ids
                else []
            )
            retrievals_raw = (
                list(
                    session.scalars(
                        select(RetrievalRunRecord)
                        .where(RetrievalRunRecord.conversation_id.in_(conversation_ids))
                        .order_by(
                            RetrievalRunRecord.created_at,
                            RetrievalRunRecord.id,
                        )
                    )
                )
                if conversation_ids
                else []
            )
            retrieval_ids = [item.id for item in retrievals_raw]
            model_calls = (
                self._records(
                    session,
                    select(PersonaModelCallRecord)
                    .where(PersonaModelCallRecord.retrieval_run_id.in_(retrieval_ids))
                    .order_by(
                        PersonaModelCallRecord.created_at,
                        PersonaModelCallRecord.id,
                    ),
                )
                if retrieval_ids
                else []
            )
            citations = (
                self._records(
                    session,
                    select(AnswerCitationRecord)
                    .where(AnswerCitationRecord.retrieval_run_id.in_(retrieval_ids))
                    .order_by(
                        AnswerCitationRecord.created_at,
                        AnswerCitationRecord.id,
                    ),
                )
                if retrieval_ids
                else []
            )
            embedding_rows = (
                list(
                    session.scalars(
                        select(PersonaMemoryEmbeddingRecord)
                        .where(PersonaMemoryEmbeddingRecord.memory_id.in_(memory_ids))
                        .order_by(
                            PersonaMemoryEmbeddingRecord.created_at,
                            PersonaMemoryEmbeddingRecord.id,
                        )
                    )
                )
                if memory_ids
                else []
            )
            embedding_metadata = []
            for row in embedding_rows:
                item = _record_dict(row)
                item.pop("embedding", None)
                embedding_metadata.append(item)
            space_ids = sorted(
                {item.embedding_space_id for item in embedding_rows}
                | {item.embedding_space_id for item in retrievals_raw}
            )
            embedding_spaces = (
                self._records(
                    session,
                    select(EmbeddingSpaceRecord).where(
                        EmbeddingSpaceRecord.id.in_(space_ids)
                    ),
                )
                if space_ids
                else []
            )
            audit_events = self._records(
                session,
                select(AuditEventRecord)
                .where(
                    AuditEventRecord.persona_id == persona.id,
                    AuditEventRecord.owner_id == access.owner_id,
                )
                .order_by(AuditEventRecord.occurred_at, AuditEventRecord.id),
            )
            document_output = []
            blob_sources = []
            for document in documents:
                public = _record_dict(document)
                object_key = public.pop("object_key")
                document_output.append(public)
                blob_sources.append(
                    {
                        "document_id": document.id,
                        "object_key": object_key,
                        "content_sha256": document.content_sha256,
                    }
                )
            return {
                "schema_version": "persona-export-v1",
                "exported_at": utc_now().isoformat(),
                "persona": _record_dict(persona),
                "source_documents": document_output,
                "document_chunks": chunks,
                "memories": [_record_dict(item) for item in memories_raw],
                "memory_versions": [_record_dict(item) for item in versions_raw],
                "memory_evidence": evidence,
                "memory_relations": relations,
                "embedding_spaces": embedding_spaces,
                "memory_embedding_metadata": embedding_metadata,
                "conversations": [_record_dict(item) for item in conversations_raw],
                "conversation_messages": messages,
                "retrieval_runs": [_record_dict(item) for item in retrievals_raw],
                "model_calls": model_calls,
                "answer_citations": citations,
                "audit_events": audit_events,
                "_blob_sources": blob_sources,
            }

    def record_export(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        export_sha256: str,
        byte_size: int,
        included_raw_sources: bool,
    ) -> str:
        with self.database.session(owner_id=access.owner_id) as session:
            self._owned_persona(session, access, persona_id)
            return self._add_audit(
                session,
                access=access,
                persona_id=persona_id,
                action="persona.exported",
                resource_type="persona",
                resource_id=persona_id,
                dedupe_key=(f"persona.exported:{persona_id}:{export_sha256}"),
                risk_level="high",
                after_hash=export_sha256,
                detail={
                    "export_sha256": export_sha256,
                    "byte_size": byte_size,
                    "included_raw_sources": included_raw_sources,
                    "schema_version": "persona-export-v1",
                },
            )

    @staticmethod
    def _summary(content: str) -> str:
        summary = " ".join(content.split())
        return f"{summary[:279]}…" if len(summary) > 280 else summary

    @staticmethod
    def _records(session: Session, statement: Any) -> list[dict[str, Any]]:
        return [_record_dict(item) for item in session.scalars(statement)]

    @staticmethod
    def _delete_count(
        session: Session,
        model: Any,
        condition: Any,
    ) -> int:
        result = session.execute(delete(model).where(condition))
        return int(result.rowcount or 0)

    @staticmethod
    def _owned_persona(
        session: Session,
        access: AccessContext,
        persona_id: str,
    ) -> PersonaRecord:
        persona = session.get(PersonaRecord, persona_id)
        if persona is None or persona.owner_id != access.owner_id:
            raise KeyError(f"PersonaRecord not found: {persona_id}")
        return persona

    @staticmethod
    def _owned_persona_for_update(
        session: Session,
        access: AccessContext,
        persona_id: str,
    ) -> PersonaRecord:
        persona = session.scalar(
            select(PersonaRecord)
            .where(
                PersonaRecord.id == persona_id,
                PersonaRecord.owner_id == access.owner_id,
            )
            .with_for_update()
        )
        if persona is None:
            raise KeyError(f"PersonaRecord not found: {persona_id}")
        return persona

    @staticmethod
    def _owned_memory(
        session: Session,
        access: AccessContext,
        memory_id: str,
    ) -> PersonaMemoryRecord:
        memory = session.get(PersonaMemoryRecord, memory_id)
        if memory is None or memory.owner_id != access.owner_id:
            raise KeyError(f"PersonaMemoryRecord not found: {memory_id}")
        return memory

    @staticmethod
    def _owned_memory_for_update(
        session: Session,
        access: AccessContext,
        memory_id: str,
    ) -> PersonaMemoryRecord:
        memory = session.scalar(
            select(PersonaMemoryRecord)
            .where(
                PersonaMemoryRecord.id == memory_id,
                PersonaMemoryRecord.owner_id == access.owner_id,
            )
            .with_for_update()
        )
        if memory is None:
            raise KeyError(f"PersonaMemoryRecord not found: {memory_id}")
        return memory

    @staticmethod
    def _owned_document_for_update(
        session: Session,
        access: AccessContext,
        document_id: str,
    ) -> SourceDocumentRecord:
        document = session.scalar(
            select(SourceDocumentRecord)
            .where(
                SourceDocumentRecord.id == document_id,
                SourceDocumentRecord.owner_id == access.owner_id,
            )
            .with_for_update()
        )
        if document is None:
            raise KeyError(f"SourceDocumentRecord not found: {document_id}")
        return document

    @staticmethod
    def _current_version(
        session: Session,
        memory: PersonaMemoryRecord,
    ) -> PersonaMemoryVersionRecord:
        version = session.get(
            PersonaMemoryVersionRecord,
            memory.current_version_id,
        )
        if version is None or version.memory_id != memory.id:
            raise ValueError(f"Memory {memory.id} has an invalid current version")
        return version

    @staticmethod
    def _validate_relation_evidence(
        session: Session,
        *,
        memory_ids: set[str],
        version_ids: list[str],
    ) -> None:
        if len(set(version_ids)) != len(version_ids):
            raise ValueError("evidence_memory_version_ids must be unique")
        if not version_ids:
            return
        rows = list(
            session.execute(
                select(
                    PersonaMemoryVersionRecord.id,
                    PersonaMemoryVersionRecord.memory_id,
                ).where(PersonaMemoryVersionRecord.id.in_(version_ids))
            )
        )
        if len(rows) != len(version_ids):
            raise ValueError("A relation evidence memory version does not exist")
        if any(memory_id not in memory_ids for _, memory_id in rows):
            raise ValueError(
                "Relation evidence versions must belong to one of the related memories"
            )

    @staticmethod
    def _delete_relations_for_memories(
        session: Session,
        memory_ids: set[str],
    ) -> int:
        if not memory_ids:
            return 0
        result = session.execute(
            delete(PersonaMemoryRelationRecord).where(
                or_(
                    PersonaMemoryRelationRecord.from_memory_id.in_(memory_ids),
                    PersonaMemoryRelationRecord.to_memory_id.in_(memory_ids),
                )
            )
        )
        return int(result.rowcount or 0)

    @staticmethod
    def _redact_dependent_answers(
        session: Session,
        *,
        owner_id: str,
        persona_id: str,
        memory_ids: set[str],
        memory_version_ids: set[str],
        source_document_id: str | None,
    ) -> dict[str, int]:
        retrieval_ids: set[str] = set()
        retrievals = list(
            session.scalars(
                select(RetrievalRunRecord).where(
                    RetrievalRunRecord.owner_id == owner_id,
                    RetrievalRunRecord.persona_id == persona_id,
                )
            )
        )
        for retrieval in retrievals:
            if any(
                str(candidate.get("memory_id")) in memory_ids
                or str(candidate.get("memory_version_id")) in memory_version_ids
                for candidate in retrieval.candidates or []
            ):
                retrieval_ids.add(retrieval.id)

        citation_conditions = []
        if memory_ids:
            citation_conditions.append(AnswerCitationRecord.memory_id.in_(memory_ids))
        if memory_version_ids:
            citation_conditions.append(
                AnswerCitationRecord.memory_version_id.in_(memory_version_ids)
            )
        if source_document_id is not None:
            citation_conditions.append(
                AnswerCitationRecord.source_document_id == source_document_id
            )
        matching_citations = (
            list(
                session.scalars(
                    select(AnswerCitationRecord).where(or_(*citation_conditions))
                )
            )
            if citation_conditions
            else []
        )
        retrieval_ids.update(item.retrieval_run_id for item in matching_citations)
        assistant_ids = {item.assistant_message_id for item in matching_citations}
        if retrieval_ids:
            model_calls = list(
                session.scalars(
                    select(PersonaModelCallRecord).where(
                        PersonaModelCallRecord.retrieval_run_id.in_(retrieval_ids)
                    )
                )
            )
            assistant_ids.update(
                item.assistant_message_id
                for item in model_calls
                if item.assistant_message_id is not None
            )
        else:
            model_calls = []
        citation_count = 0
        if assistant_ids:
            citation_count = PersonaLifecycleRepository._delete_count(
                session,
                AnswerCitationRecord,
                AnswerCitationRecord.assistant_message_id.in_(assistant_ids),
            )
        elif citation_conditions:
            citation_count = PersonaLifecycleRepository._delete_count(
                session,
                AnswerCitationRecord,
                or_(*citation_conditions),
            )

        messages = (
            list(
                session.scalars(
                    select(ConversationMessageRecord).where(
                        ConversationMessageRecord.id.in_(assistant_ids)
                    )
                )
            )
            if assistant_ids
            else []
        )
        for message in messages:
            message.content = DELETED_ANSWER_NOTICE
            message.claims = []
            message.uncertainty = {
                "level": "source_deleted",
                "message": "原回答所依赖的记忆或来源已删除。",
            }
        for model_call in model_calls:
            model_call.response_hash = None
            model_call.status = "failed"
            model_call.error_type = "source_deleted"
            model_call.usage = {}
        invalidated_runs = [item for item in retrievals if item.id in retrieval_ids]
        for retrieval in invalidated_runs:
            retrieval.status = "failed"
            retrieval.candidates = []
            retrieval.filters = {
                **(retrieval.filters or {}),
                "invalidated_by_deletion": True,
            }
        return {
            "redacted_answer_count": len(messages),
            "deleted_citation_count": citation_count,
            "invalidated_retrieval_count": len(invalidated_runs),
            "invalidated_model_call_count": len(model_calls),
        }

    @staticmethod
    def _deletion_receipt(
        session: Session,
        access: AccessContext,
        *,
        action: str,
        resource_id: str,
    ) -> dict[str, Any] | None:
        event = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.owner_id == access.owner_id,
                AuditEventRecord.action == action,
                AuditEventRecord.resource_id == resource_id,
            )
        )
        if event is None:
            return None
        return {
            "id": resource_id,
            "deleted": True,
            "idempotency_replayed": True,
            "audit_event_id": event.id,
            **(event.detail or {}),
        }

    @staticmethod
    def _add_audit(
        session: Session,
        *,
        access: AccessContext,
        persona_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str,
        dedupe_key: str,
        risk_level: str = "low",
        before_hash: str | None = None,
        after_hash: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> str:
        existing = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.owner_id == access.owner_id,
                AuditEventRecord.dedupe_key == dedupe_key,
            )
        )
        if existing is not None:
            return existing
        record = AuditEventRecord(
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
            risk_level=risk_level,
            before_hash=before_hash,
            after_hash=after_hash,
            detail=detail or {},
        )
        session.add(record)
        session.flush()
        return record.id
