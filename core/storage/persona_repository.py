from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.ingestion.models import ChunkDraft, MemoryCandidateDraft
from core.security.access import AccessContext
from core.storage.database import Database
from core.storage.models import (
    AuditEventRecord,
    DocumentChunkRecord,
    PersonaMemoryEvidenceRecord,
    PersonaMemoryRecord,
    PersonaMemoryVersionRecord,
    PersonaRecord,
    SourceDocumentRecord,
    UserRecord,
    utc_now,
)

SIMULATION_NOTICE = (
    "这是一个依据用户授权资料生成的模拟智能体，不是现实中的本人；"
    "其回答可能不完整或不准确，应结合引用来源判断。"
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


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class PersonaRepository:
    """Persistence boundary for persona evidence and append-only memory versions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_persona(
        self,
        access: AccessContext,
        *,
        display_name: str,
        description: str,
    ) -> dict[str, Any]:
        persona_id = _new_id()
        now = utc_now()
        with self.database.session() as session:
            self._ensure_user(session, access.owner_id)
            persona = PersonaRecord(
                id=persona_id,
                owner_id=access.owner_id,
                display_name=display_name,
                description=description,
                simulation_notice=SIMULATION_NOTICE,
                status="active",
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(persona)
            session.flush()
            self._add_audit_event(
                session,
                access=access,
                persona_id=persona_id,
                action="persona.created",
                resource_type="persona",
                resource_id=persona_id,
                dedupe_key=f"persona.created:{persona_id}",
                after_hash=_payload_hash(
                    {
                        "display_name": display_name,
                        "description": description,
                        "status": "active",
                        "version": 1,
                    }
                ),
                detail={"version": 1},
            )
            return _record_dict(persona)

    def list_personas(
        self,
        access: AccessContext,
        *,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            statement = select(PersonaRecord).where(
                PersonaRecord.owner_id == access.owner_id
            )
            if not include_inactive:
                statement = statement.where(PersonaRecord.status == "active")
            records = session.scalars(
                statement.order_by(PersonaRecord.created_at, PersonaRecord.id)
            )
            return [_record_dict(record) for record in records]

    def get_persona(
        self,
        access: AccessContext,
        persona_id: str,
        *,
        require_active: bool = False,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            persona = self._owned_persona(
                session,
                access,
                persona_id,
                require_active=require_active,
            )
            return _record_dict(persona)

    def upsert_document(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        original_filename: str,
        media_type: str,
        object_key: str,
        content_sha256: str,
        byte_size: int,
        language: str | None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.session() as session:
            self._owned_persona(
                session,
                access,
                persona_id,
                require_active=True,
            )
            document = session.scalar(
                select(SourceDocumentRecord).where(
                    SourceDocumentRecord.persona_id == persona_id,
                    SourceDocumentRecord.content_sha256 == content_sha256,
                )
            )
            created = document is None
            if document is None:
                document = SourceDocumentRecord(
                    id=_new_id(),
                    persona_id=persona_id,
                    owner_id=access.owner_id,
                    source_type="uploaded_text",
                    original_filename=original_filename,
                    media_type=media_type,
                    language=language,
                    object_key=object_key,
                    content_sha256=content_sha256,
                    byte_size=byte_size,
                    status="uploaded",
                    ingestion_version="text-v1",
                    created_at=now,
                    updated_at=now,
                )
                session.add(document)
                session.flush()
                self._add_audit_event(
                    session,
                    access=access,
                    persona_id=persona_id,
                    action="document.uploaded",
                    resource_type="source_document",
                    resource_id=document.id,
                    dedupe_key=f"document.uploaded:{document.id}",
                    after_hash=content_sha256,
                    detail={
                        "filename": original_filename,
                        "media_type": media_type,
                        "byte_size": byte_size,
                        "content_sha256": content_sha256,
                    },
                )
            return {
                "document": self._public_document(document),
                "created": created,
            }

    def attach_document_task(
        self,
        access: AccessContext,
        document_id: str,
        *,
        task_id: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            document = self._owned_document(session, access, document_id)
            if document.task_id is None:
                document.task_id = task_id
                document.updated_at = utc_now()
            elif document.task_id != task_id:
                raise ValueError(
                    f"Document {document_id} is already attached to another task"
                )
            session.flush()
            return self._public_document(document)

    def get_document(
        self,
        access: AccessContext,
        document_id: str,
        *,
        include_chunks: bool = True,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            document = self._owned_document(session, access, document_id)
            chunks = (
                list(
                    session.scalars(
                        select(DocumentChunkRecord)
                        .where(DocumentChunkRecord.document_id == document_id)
                        .order_by(DocumentChunkRecord.ordinal)
                    )
                )
                if include_chunks
                else []
            )
            return {
                "document": self._public_document(document),
                "chunks": [_record_dict(item) for item in chunks],
            }

    def list_documents(
        self,
        access: AccessContext,
        *,
        persona_id: str,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            self._owned_persona(session, access, persona_id)
            documents = session.scalars(
                select(SourceDocumentRecord)
                .where(
                    SourceDocumentRecord.persona_id == persona_id,
                    SourceDocumentRecord.owner_id == access.owner_id,
                    SourceDocumentRecord.status != "deleted",
                )
                .order_by(SourceDocumentRecord.created_at, SourceDocumentRecord.id)
            )
            return [self._public_document(item) for item in documents]

    def get_document_for_processing(
        self,
        *,
        owner_id: str,
        document_id: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            document = session.get(SourceDocumentRecord, document_id)
            if document is None or document.owner_id != owner_id:
                raise KeyError(f"SourceDocumentRecord not found: {document_id}")
            return _record_dict(document)

    def mark_document_processing(
        self,
        *,
        owner_id: str,
        document_id: str,
    ) -> None:
        with self.database.session() as session:
            document = session.get(SourceDocumentRecord, document_id)
            if document is None or document.owner_id != owner_id:
                raise KeyError(f"SourceDocumentRecord not found: {document_id}")
            if document.status == "ready":
                return
            if document.status in {"deleting", "deleted"}:
                raise ValueError(
                    f"Document {document_id} cannot be processed from {document.status}"
                )
            document.status = "processing"
            document.error = None
            document.updated_at = utc_now()

    def mark_document_failed(
        self,
        *,
        access: AccessContext,
        document_id: str,
        task_run_id: str,
        error: str,
    ) -> None:
        with self.database.session() as session:
            document = self._owned_document(session, access, document_id)
            if document.status == "ready":
                return
            document.status = "failed"
            document.error = error[:2000]
            document.updated_at = utc_now()
            self._add_audit_event(
                session,
                access=access,
                persona_id=document.persona_id,
                action="document.processing_failed",
                resource_type="source_document",
                resource_id=document.id,
                dedupe_key=f"document.failed:{document.id}:{task_run_id}",
                outcome="failed",
                risk_level="medium",
                detail={
                    "task_run_id": task_run_id,
                    "error_type": error.split(":", maxsplit=1)[0][:120],
                },
            )

    def persist_ingestion(
        self,
        *,
        access: AccessContext,
        document_id: str,
        chunks: list[ChunkDraft],
        candidates: list[MemoryCandidateDraft],
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.session() as session:
            document = self._owned_document(session, access, document_id)
            persona = self._owned_persona(
                session,
                access,
                document.persona_id,
                require_active=True,
            )
            chunk_records: dict[int, DocumentChunkRecord] = {}
            chunks_created = 0
            for chunk in chunks:
                record = session.scalar(
                    select(DocumentChunkRecord).where(
                        DocumentChunkRecord.document_id == document.id,
                        DocumentChunkRecord.ordinal == chunk.ordinal,
                    )
                )
                if record is None:
                    record = DocumentChunkRecord(
                        id=_new_id(),
                        document_id=document.id,
                        persona_id=persona.id,
                        ordinal=chunk.ordinal,
                        content=chunk.content,
                        content_sha256=chunk.content_sha256,
                        char_start=chunk.char_start,
                        char_end=chunk.char_end,
                        line_start=chunk.line_start,
                        line_end=chunk.line_end,
                        locator=chunk.locator,
                        chunker_name=chunk.chunker_name,
                        chunker_version=chunk.chunker_version,
                        chunker_config_hash=chunk.chunker_config_hash,
                    )
                    session.add(record)
                    session.flush()
                    chunks_created += 1
                elif (
                    record.content_sha256 != chunk.content_sha256
                    or record.char_start != chunk.char_start
                    or record.char_end != chunk.char_end
                ):
                    raise ValueError(
                        f"Chunk {chunk.ordinal} does not match persisted source evidence"
                    )
                chunk_records[chunk.ordinal] = record

            memories_created = 0
            evidence_created = 0
            memory_ids: list[str] = []
            for candidate in candidates:
                try:
                    chunk_record = chunk_records[candidate.chunk_ordinal]
                except KeyError as exc:
                    raise ValueError(
                        f"Candidate references unknown chunk {candidate.chunk_ordinal}"
                    ) from exc
                persisted_fingerprint = sha256(
                    (f"{document.id}\0{candidate.candidate_fingerprint}").encode()
                ).hexdigest()
                memory = session.scalar(
                    select(PersonaMemoryRecord).where(
                        PersonaMemoryRecord.persona_id == persona.id,
                        PersonaMemoryRecord.candidate_fingerprint
                        == persisted_fingerprint,
                    )
                )
                if memory is None:
                    memory = PersonaMemoryRecord(
                        id=_new_id(),
                        persona_id=persona.id,
                        owner_id=access.owner_id,
                        source_document_id=document.id,
                        memory_type=candidate.memory_type,
                        status="candidate",
                        epistemic_status=candidate.epistemic_status,
                        candidate_fingerprint=persisted_fingerprint,
                        confidence=candidate.confidence,
                        importance=candidate.importance,
                        sensitivity=candidate.sensitivity,
                        visibility=candidate.visibility,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(memory)
                    session.flush()
                    version = PersonaMemoryVersionRecord(
                        id=_new_id(),
                        memory_id=memory.id,
                        version=1,
                        raw_content=candidate.raw_content,
                        structured_summary=candidate.structured_summary,
                        metadata_snapshot={
                            "memory_type": candidate.memory_type,
                            "epistemic_status": candidate.epistemic_status,
                            "source_document_id": document.id,
                            "source_chunk_id": chunk_record.id,
                            "extractor_candidate_fingerprint": (
                                candidate.candidate_fingerprint
                            ),
                            "source_bound": True,
                            "user_confirmed": False,
                            "is_model_inference": False,
                        },
                        created_by_type="extractor",
                        created_by_id=(
                            f"{candidate.extractor_name}@{candidate.extractor_version}"
                        ),
                        change_reason="initial memory candidate extraction",
                        extractor_name=candidate.extractor_name,
                        extractor_version=candidate.extractor_version,
                    )
                    session.add(version)
                    session.flush()
                    memory.current_version_id = version.id
                    memories_created += 1
                else:
                    version = self._current_version(session, memory)

                evidence = session.scalar(
                    select(PersonaMemoryEvidenceRecord).where(
                        PersonaMemoryEvidenceRecord.memory_version_id == version.id,
                        PersonaMemoryEvidenceRecord.document_chunk_id
                        == chunk_record.id,
                        PersonaMemoryEvidenceRecord.relation == "supports",
                    )
                )
                if evidence is None:
                    excerpt = chunk_record.content.strip()
                    if len(excerpt) > 1000:
                        excerpt = f"{excerpt[:999]}…"
                    session.add(
                        PersonaMemoryEvidenceRecord(
                            id=_new_id(),
                            memory_version_id=version.id,
                            source_document_id=document.id,
                            document_chunk_id=chunk_record.id,
                            relation="supports",
                            locator_snapshot=chunk_record.locator,
                            excerpt=excerpt,
                            excerpt_sha256=sha256(excerpt.encode("utf-8")).hexdigest(),
                        )
                    )
                    evidence_created += 1
                memory_ids.append(memory.id)

            document.status = "ready"
            document.error = None
            document.processed_at = now
            document.updated_at = now
            self._add_audit_event(
                session,
                access=access,
                persona_id=persona.id,
                action="document.processed",
                resource_type="source_document",
                resource_id=document.id,
                dedupe_key=(
                    f"document.processed:{document.id}:{document.ingestion_version}"
                ),
                after_hash=document.content_sha256,
                detail={
                    "chunk_count": len(chunk_records),
                    "candidate_count": len(set(memory_ids)),
                    "chunks_created": chunks_created,
                    "memories_created": memories_created,
                    "evidence_created": evidence_created,
                    "ingestion_version": document.ingestion_version,
                },
            )
            session.flush()
            return {
                "document_id": document.id,
                "persona_id": persona.id,
                "status": document.status,
                "chunk_count": len(chunk_records),
                "candidate_count": len(set(memory_ids)),
                "memory_ids": sorted(set(memory_ids)),
                "chunks_created": chunks_created,
                "memories_created": memories_created,
                "evidence_created": evidence_created,
            }

    def list_memory_bundles(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        status: str,
    ) -> list[dict[str, Any]]:
        if status not in {
            "candidate",
            "confirmed",
            "rejected",
            "superseded",
            "deleted",
        }:
            raise ValueError(f"Unsupported persona memory status: {status}")
        with self.database.session() as session:
            self._owned_persona(session, access, persona_id)
            memories = list(
                session.scalars(
                    select(PersonaMemoryRecord)
                    .where(
                        PersonaMemoryRecord.persona_id == persona_id,
                        PersonaMemoryRecord.owner_id == access.owner_id,
                        PersonaMemoryRecord.status == status,
                    )
                    .order_by(
                        PersonaMemoryRecord.created_at,
                        PersonaMemoryRecord.id,
                    )
                )
            )
            return [self._memory_bundle(session, memory) for memory in memories]

    def get_memory_bundle(
        self,
        access: AccessContext,
        memory_id: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            memory = self._owned_memory(session, access, memory_id)
            return self._memory_bundle(session, memory)

    def review_memory(
        self,
        access: AccessContext,
        memory_id: str,
        *,
        action: str,
        edited_content: str | None,
        reason: str | None,
    ) -> dict[str, Any]:
        if action not in {"confirm", "reject"}:
            raise ValueError(f"Unsupported memory review action: {action}")
        if action == "reject" and edited_content is not None:
            raise ValueError("edited_content is only valid when confirming")
        target_status = "confirmed" if action == "confirm" else "rejected"
        now = utc_now()
        with self.database.session() as session:
            memory = self._owned_memory_for_update(
                session,
                access,
                memory_id,
            )
            if memory.status == target_status:
                if action == "confirm" and edited_content is not None:
                    current = self._current_version(session, memory)
                    if edited_content.strip() != current.raw_content:
                        raise ValueError(
                            "Memory is already confirmed with different content"
                        )
                return self._memory_bundle(session, memory)
            if memory.status != "candidate":
                raise ValueError(
                    f"Memory {memory_id} cannot {action} from status {memory.status}"
                )
            current = self._current_version(session, memory)
            before_hash = _payload_hash(
                {
                    "status": memory.status,
                    "current_version_id": current.id,
                    "version": current.version,
                }
            )
            edited = False
            if action == "confirm":
                confirmed_content = current.raw_content
                if edited_content is not None:
                    confirmed_content = edited_content.strip()
                    if not confirmed_content:
                        raise ValueError("edited_content must not be empty")
                edited = confirmed_content != current.raw_content
                current = self._create_confirmed_version(
                    session,
                    access=access,
                    memory=memory,
                    source_version=current,
                    content=confirmed_content,
                    edited=edited,
                    reason=reason,
                )

            memory.status = target_status
            memory.updated_at = now
            if action == "confirm":
                memory.confirmed_by = access.actor_id
                memory.confirmed_at = now
            after_hash = _payload_hash(
                {
                    "status": target_status,
                    "current_version_id": current.id,
                    "version": current.version,
                }
            )
            self._add_audit_event(
                session,
                access=access,
                persona_id=memory.persona_id,
                action=f"memory.{target_status}",
                resource_type="persona_memory",
                resource_id=memory.id,
                dedupe_key=f"memory.review:{memory.id}:{target_status}",
                before_hash=before_hash,
                after_hash=after_hash,
                risk_level="medium",
                detail={
                    "previous_status": "candidate",
                    "new_status": target_status,
                    "memory_version": current.version,
                    "edited": edited,
                    "reason_provided": bool((reason or "").strip()),
                },
            )
            session.flush()
            return self._memory_bundle(session, memory)

    @staticmethod
    def _create_confirmed_version(
        session: Session,
        *,
        access: AccessContext,
        memory: PersonaMemoryRecord,
        source_version: PersonaMemoryVersionRecord,
        content: str,
        edited: bool,
        reason: str | None,
    ) -> PersonaMemoryVersionRecord:
        summary = " ".join(content.split())
        if len(summary) > 280:
            summary = f"{summary[:279]}…"
        confirmed = PersonaMemoryVersionRecord(
            id=_new_id(),
            memory_id=memory.id,
            version=source_version.version + 1,
            raw_content=content,
            structured_summary=summary,
            metadata_snapshot={
                **(source_version.metadata_snapshot or {}),
                "user_confirmed": True,
                "confirmed_from_version": source_version.version,
                "content_edited_during_confirmation": edited,
            },
            created_by_type=access.actor_type,
            created_by_id=access.actor_id,
            change_reason=reason or "confirmed by user",
            extractor_name="human-review",
            extractor_version="1.0.0",
        )
        session.add(confirmed)
        session.flush()
        evidence_records = list(
            session.scalars(
                select(PersonaMemoryEvidenceRecord).where(
                    PersonaMemoryEvidenceRecord.memory_version_id == source_version.id
                )
            )
        )
        for evidence in evidence_records:
            session.add(
                PersonaMemoryEvidenceRecord(
                    id=_new_id(),
                    memory_version_id=confirmed.id,
                    source_document_id=evidence.source_document_id,
                    document_chunk_id=evidence.document_chunk_id,
                    relation=evidence.relation,
                    locator_snapshot=evidence.locator_snapshot,
                    excerpt=evidence.excerpt,
                    excerpt_sha256=evidence.excerpt_sha256,
                )
            )
        memory.current_version_id = confirmed.id
        return confirmed

    def list_audit_events(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            self._owned_persona(session, access, persona_id)
            records = list(
                session.scalars(
                    select(AuditEventRecord)
                    .where(
                        AuditEventRecord.owner_id == access.owner_id,
                        AuditEventRecord.persona_id == persona_id,
                    )
                    .order_by(
                        AuditEventRecord.occurred_at.desc(),
                        AuditEventRecord.id.desc(),
                    )
                    .limit(max(1, min(limit, 500)))
                )
            )
            records.reverse()
            return [_record_dict(item) for item in records]

    @staticmethod
    def _ensure_user(session: Session, owner_id: str) -> None:
        if session.get(UserRecord, owner_id) is None:
            session.add(UserRecord(id=owner_id, display_name=owner_id))
            session.flush()

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
    def _owned_document(
        session: Session,
        access: AccessContext,
        document_id: str,
    ) -> SourceDocumentRecord:
        document = session.get(SourceDocumentRecord, document_id)
        if document is None or document.owner_id != access.owner_id:
            raise KeyError(f"SourceDocumentRecord not found: {document_id}")
        return document

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
    def _current_version(
        session: Session,
        memory: PersonaMemoryRecord,
    ) -> PersonaMemoryVersionRecord:
        if memory.current_version_id is None:
            raise ValueError(f"Memory {memory.id} has no current version")
        version = session.get(
            PersonaMemoryVersionRecord,
            memory.current_version_id,
        )
        if version is None or version.memory_id != memory.id:
            raise ValueError(f"Memory {memory.id} has an invalid current version")
        return version

    def _memory_bundle(
        self,
        session: Session,
        memory: PersonaMemoryRecord,
    ) -> dict[str, Any]:
        version = self._current_version(session, memory)
        versions = list(
            session.scalars(
                select(PersonaMemoryVersionRecord)
                .where(PersonaMemoryVersionRecord.memory_id == memory.id)
                .order_by(PersonaMemoryVersionRecord.version)
            )
        )
        evidence_records = list(
            session.scalars(
                select(PersonaMemoryEvidenceRecord)
                .where(PersonaMemoryEvidenceRecord.memory_version_id == version.id)
                .order_by(PersonaMemoryEvidenceRecord.created_at)
            )
        )
        evidence: list[dict[str, Any]] = []
        for item in evidence_records:
            document = session.get(SourceDocumentRecord, item.source_document_id)
            chunk = session.get(DocumentChunkRecord, item.document_chunk_id)
            if document is None or chunk is None:
                raise ValueError(f"Memory evidence {item.id} has a missing source")
            evidence.append(
                {
                    "evidence": _record_dict(item),
                    "source_document": self._public_document(document),
                    "document_chunk": _record_dict(chunk),
                }
            )
        return {
            "memory": _record_dict(memory),
            "current_version": _record_dict(version),
            "versions": [_record_dict(item) for item in versions],
            "evidence": evidence,
        }

    @staticmethod
    def _public_document(document: SourceDocumentRecord) -> dict[str, Any]:
        result = _record_dict(document)
        result.pop("object_key", None)
        return result

    @staticmethod
    def _add_audit_event(
        session: Session,
        *,
        access: AccessContext,
        persona_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str,
        dedupe_key: str,
        outcome: str = "succeeded",
        risk_level: str = "low",
        before_hash: str | None = None,
        after_hash: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        existing = session.scalar(
            select(AuditEventRecord.id).where(
                AuditEventRecord.owner_id == access.owner_id,
                AuditEventRecord.dedupe_key == dedupe_key,
            )
        )
        if existing is not None:
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
                outcome=outcome,
                risk_level=risk_level,
                before_hash=before_hash,
                after_hash=after_hash,
                detail=detail or {},
            )
        )
