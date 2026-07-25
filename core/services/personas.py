from __future__ import annotations

import json
import threading
from hashlib import sha256
from pathlib import Path
from typing import Any

from core.retrieval.service import MemoryIndexService
from core.security.access import AccessContext
from core.security.data_policy import (
    normalize_model_boundaries,
    normalize_sensitivity,
)
from core.storage.blob import BlobStore
from core.storage.persona_lifecycle_repository import (
    PersonaLifecycleRepository,
)
from core.storage.persona_repository import PersonaRepository
from core.storage.repository import ExecutionStore

PERSONA_INGESTION_EMPLOYEE_ID = "persona-memory-curator-001"
PERSONA_INGESTION_WORKFLOW = "persona-text-ingestion"


class PersonaService:
    def __init__(
        self,
        *,
        repository: PersonaRepository,
        execution_store: ExecutionStore,
        blob_store: BlobStore,
        lifecycle_repository: PersonaLifecycleRepository | None = None,
        memory_index: MemoryIndexService | None = None,
        max_upload_bytes: int = 5 * 1024 * 1024,
        max_export_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        self._repository = repository
        self._execution_store = execution_store
        self._blob_store = blob_store
        self._lifecycle = lifecycle_repository
        self._memory_index = memory_index
        self._max_upload_bytes = max(1, max_upload_bytes)
        self._max_export_bytes = max(1, max_export_bytes)
        self._blob_lock = threading.RLock()

    def create(
        self,
        access: AccessContext,
        *,
        display_name: str,
        description: str = "",
    ) -> dict[str, Any]:
        normalized_name = " ".join(display_name.split())
        if not normalized_name:
            raise ValueError("display_name must not be empty")
        if len(normalized_name) > 200:
            raise ValueError("display_name must not exceed 200 characters")
        normalized_description = description.strip()
        if len(normalized_description) > 10_000:
            raise ValueError("description must not exceed 10000 characters")
        return self._repository.create_persona(
            access,
            display_name=normalized_name,
            description=normalized_description,
        )

    def list(
        self,
        access: AccessContext,
        *,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        return self._repository.list_personas(
            access,
            include_inactive=include_inactive,
        )

    def get(
        self,
        access: AccessContext,
        persona_id: str,
    ) -> dict[str, Any]:
        return self._repository.get_persona(access, persona_id)

    def upload_text(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        filename: str,
        media_type: str | None,
        content: bytes,
        language: str | None = None,
    ) -> dict[str, Any]:
        if not content:
            raise ValueError("uploaded document must not be empty")
        if len(content) > self._max_upload_bytes:
            raise ValueError(
                f"uploaded document exceeds {self._max_upload_bytes} bytes"
            )
        safe_filename = Path(filename or "").name
        if not safe_filename or len(safe_filename) > 255:
            raise ValueError("uploaded document must have a valid filename")
        extension = Path(safe_filename).suffix.casefold()
        if extension not in {".txt", ".md"}:
            raise ValueError("only .txt and .md documents are supported")
        normalized_media_type = (media_type or "").split(";", maxsplit=1)[0].strip()
        if not normalized_media_type:
            normalized_media_type = (
                "text/markdown" if extension == ".md" else "text/plain"
            )
        if normalized_media_type not in {
            "text/plain",
            "text/markdown",
            "application/octet-stream",
        }:
            raise ValueError(
                f"unsupported document media type: {normalized_media_type}"
            )
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("uploaded document must be valid UTF-8 text") from exc
        if "\x00" in decoded:
            raise ValueError("uploaded document must not contain NUL bytes")
        if not decoded.strip():
            raise ValueError("uploaded document must contain non-whitespace text")
        normalized_language = (language or "").strip() or None
        if normalized_language is not None and len(normalized_language) > 40:
            raise ValueError("language must not exceed 40 characters")

        self._repository.get_persona(
            access,
            persona_id,
            require_active=True,
        )
        with self._blob_lock:
            blob = self._blob_store.put(content)
            document_result = self._repository.upsert_document(
                access,
                persona_id=persona_id,
                original_filename=safe_filename,
                media_type=normalized_media_type,
                object_key=blob.object_key,
                content_sha256=blob.content_sha256,
                byte_size=blob.byte_size,
                language=normalized_language,
            )
        document = document_result["document"]
        submission = self._execution_store.create_queued_task(
            employee_id=PERSONA_INGESTION_EMPLOYEE_ID,
            user_id=access.owner_id,
            workflow_name=PERSONA_INGESTION_WORKFLOW,
            task_input={
                "persona_id": persona_id,
                "document_id": document["id"],
                "content_sha256": blob.content_sha256,
                "read_only_source": True,
            },
            idempotency_key=(f"persona-text-ingestion:{document['id']}:text-v1"),
            max_attempts=3,
        )
        document = self._repository.attach_document_task(
            access,
            document["id"],
            task_id=submission["task_id"],
        )
        return {
            "document": document,
            "document_created": document_result["created"],
            "blob_created": blob.created,
            "queue_submission": {
                "task_id": submission["task_id"],
                "queue_job_id": submission["queue_job_id"],
                "created": submission["created"],
                "idempotency_replayed": not submission["created"],
            },
            "task": self._execution_store.get_task_bundle(submission["task_id"]),
        }

    def list_documents(
        self,
        access: AccessContext,
        *,
        persona_id: str,
    ) -> list[dict[str, Any]]:
        return self._repository.list_documents(
            access,
            persona_id=persona_id,
        )

    def get_document(
        self,
        access: AccessContext,
        document_id: str,
    ) -> dict[str, Any]:
        return self._repository.get_document(access, document_id)

    def list_memories(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        status: str,
    ) -> list[dict[str, Any]]:
        return self._repository.list_memory_bundles(
            access,
            persona_id=persona_id,
            status=status,
        )

    def get_memory(
        self,
        access: AccessContext,
        memory_id: str,
    ) -> dict[str, Any]:
        return self._repository.get_memory_bundle(access, memory_id)

    def review_memory(
        self,
        access: AccessContext,
        memory_id: str,
        *,
        action: str,
        edited_content: str | None,
        reason: str | None,
    ) -> dict[str, Any]:
        if edited_content is not None and len(edited_content) > 20_000:
            raise ValueError("edited_content must not exceed 20000 characters")
        if reason is not None and len(reason) > 4000:
            raise ValueError("reason must not exceed 4000 characters")
        result = self._repository.review_memory(
            access,
            memory_id,
            action=action,
            edited_content=edited_content,
            reason=reason,
        )
        if (
            action == "confirm"
            and result["memory"]["status"] == "confirmed"
            and self._memory_index is not None
        ):
            result["indexing"] = self._memory_index.index_memory(
                access,
                memory_id,
            )
        return result

    def list_audit_events(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self._repository.list_audit_events(
            access,
            persona_id=persona_id,
            limit=limit,
        )

    def update_model_policy(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        allowed_model_boundaries: list[str],
        external_data_acknowledged: bool,
    ) -> dict[str, Any]:
        lifecycle = self._require_lifecycle()
        normalized = normalize_model_boundaries(allowed_model_boundaries)
        return lifecycle.update_model_policy(
            access,
            persona_id=persona_id,
            allowed_model_boundaries=normalized,
            external_data_acknowledged=external_data_acknowledged,
        )

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
        lifecycle = self._require_lifecycle()
        normalized_content = content.strip() if content is not None else None
        if normalized_content is not None:
            if not normalized_content:
                raise ValueError("content must not be empty")
            if len(normalized_content) > 20_000:
                raise ValueError("content must not exceed 20000 characters")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("reason must not be empty")
        if len(normalized_reason) > 4000:
            raise ValueError("reason must not exceed 4000 characters")
        normalized_sensitivity = (
            normalize_sensitivity(sensitivity)
            if sensitivity is not None
            else None
        )
        lifecycle.update_memory(
            access,
            memory_id=memory_id,
            expected_version=expected_version,
            content=normalized_content,
            sensitivity=normalized_sensitivity,
            reason=normalized_reason,
        )
        result = self._repository.get_memory_bundle(access, memory_id)
        if self._memory_index is not None:
            result["indexing"] = self._memory_index.index_memory(
                access,
                memory_id,
            )
        return result

    def delete_memory(
        self,
        access: AccessContext,
        *,
        memory_id: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("confirm=true is required for memory deletion")
        return self._require_lifecycle().delete_memory(
            access,
            memory_id=memory_id,
        )

    def delete_document(
        self,
        access: AccessContext,
        *,
        document_id: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("confirm=true is required for document deletion")
        lifecycle = self._require_lifecycle()
        receipt = lifecycle.get_document_deletion_receipt(
            access,
            document_id=document_id,
        )
        if receipt is not None:
            return receipt
        task_cancellation = self._settle_document_ingestion_task(
            access,
            document_id=document_id,
        )
        with self._blob_lock:
            prepared = lifecycle.prepare_document_deletion(
                access,
                document_id=document_id,
                ingestion_task_settled=True,
            )
            blob_shared = bool(prepared["other_blob_reference_count"])
            blob_deleted = (
                False
                if blob_shared
                else self._blob_store.delete(str(prepared["object_key"]))
            )
            result = lifecycle.purge_document(
                access,
                document_id=document_id,
                blob_deleted=blob_deleted,
                blob_shared=blob_shared,
            )
        if task_cancellation is not None:
            result["ingestion_task_cancellation"] = task_cancellation
        return result

    def create_memory_relation(
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
        return self._require_lifecycle().create_relation(
            access,
            persona_id=persona_id,
            from_memory_id=from_memory_id,
            to_memory_id=to_memory_id,
            relation=relation,
            confidence=confidence,
            evidence_memory_version_ids=evidence_memory_version_ids,
        )

    def list_memory_relations(
        self,
        access: AccessContext,
        *,
        memory_id: str,
    ) -> list[dict[str, Any]]:
        return self._require_lifecycle().list_relations(
            access,
            memory_id=memory_id,
        )

    def delete_memory_relation(
        self,
        access: AccessContext,
        *,
        relation_id: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("confirm=true is required for relation deletion")
        return self._require_lifecycle().delete_relation(
            access,
            relation_id=relation_id,
        )

    def export_persona(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        include_raw_sources: bool,
    ) -> dict[str, Any]:
        lifecycle = self._require_lifecycle()
        with self._blob_lock:
            snapshot = lifecycle.export_snapshot(
                access,
                persona_id=persona_id,
            )
            blob_sources = snapshot.pop("_blob_sources")
            snapshot["raw_sources"] = []
            if include_raw_sources:
                for item in blob_sources:
                    content = self._blob_store.get(
                        str(item["object_key"]),
                        expected_sha256=str(item["content_sha256"]),
                    )
                    snapshot["raw_sources"].append(
                        {
                            "document_id": item["document_id"],
                            "content_sha256": item["content_sha256"],
                            "content": content.decode("utf-8-sig"),
                        }
                    )
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > self._max_export_bytes:
            raise ValueError(
                f"persona export exceeds {self._max_export_bytes} bytes"
            )
        digest = sha256(encoded).hexdigest()
        audit_event_id = lifecycle.record_export(
            access,
            persona_id=persona_id,
            export_sha256=digest,
            byte_size=len(encoded),
            included_raw_sources=include_raw_sources,
        )
        return {
            "export": snapshot,
            "manifest": {
                "sha256": digest,
                "byte_size": len(encoded),
                "included_raw_sources": include_raw_sources,
                "audit_event_id": audit_event_id,
            },
        }

    def _require_lifecycle(self) -> PersonaLifecycleRepository:
        if self._lifecycle is None:
            raise RuntimeError("persona lifecycle repository is not configured")
        return self._lifecycle

    def _settle_document_ingestion_task(
        self,
        access: AccessContext,
        *,
        document_id: str,
    ) -> dict[str, Any] | None:
        document = self._repository.get_document(
            access,
            document_id,
            include_chunks=False,
        )["document"]
        task_id = document.get("task_id")
        if not task_id:
            return None
        task = self._execution_store.get_task_bundle(str(task_id))["task"]
        if task["status"] not in {"pending", "running", "cancelling"}:
            return None
        cancellation = self._execution_store.request_task_cancellation(
            str(task_id),
            requested_by=access.actor_id,
            reason="source document deletion requested",
        )
        if cancellation["status"] == "cancelling":
            raise ValueError(
                "Document ingestion cancellation is in progress; retry deletion "
                "after the worker releases the task"
            )
        return cancellation
