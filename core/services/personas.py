from __future__ import annotations

from pathlib import Path
from typing import Any

from core.retrieval.service import MemoryIndexService
from core.security.access import AccessContext
from core.storage.blob import BlobStore
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
        memory_index: MemoryIndexService | None = None,
        max_upload_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self._repository = repository
        self._execution_store = execution_store
        self._blob_store = blob_store
        self._memory_index = memory_index
        self._max_upload_bytes = max(1, max_upload_bytes)

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
