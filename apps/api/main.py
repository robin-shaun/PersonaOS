from __future__ import annotations

from dataclasses import replace
from typing import Annotated, Any

from fastapi import (
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse

from adapters.github.client import GitHubAdapterError
from adapters.hermes.client import HermesAdapterError
from apps.api.schemas import (
    ApprovalDecisionRequest,
    FeedbackCreate,
    GitHubConnectionCreate,
    PersonaConversationCreate,
    PersonaCreate,
    PersonaExportRequest,
    PersonaMemoryRelationCreate,
    PersonaMemoryReviewRequest,
    PersonaMemoryUpdateRequest,
    PersonaModelPolicyUpdateRequest,
    PersonaQuestionCreate,
    PreferenceReviewRequest,
    ProjectMaintenanceTaskCreate,
    TaskCancellationRequest,
)
from core.bootstrap import Container, build_container
from core.retrieval.answering import CitationValidationError
from core.security.data_policy import ModelDataPolicyError
from core.services.github_connections import GitHubAppNotConfiguredError
from core.services.project_maintenance import (
    ProjectMaintenanceCommand,
    TaskConflictError,
    TaskExecutionFailed,
)


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()
    app = FastAPI(
        title="PersonaOS",
        version="0.9.0",
        description=(
            "Evidence-driven digital employee and review-first persona memory "
            "with versioned edits, model data boundaries and auditable deletion."
        ),
    )
    app.state.container = container

    def persona_access(request: Request):
        request_id = (request.headers.get("X-Request-ID") or "").strip()
        if len(request_id) > 100 or any(
            character in request_id for character in "\r\n\t"
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Request-ID is invalid",
            )
        return replace(
            container.persona_access,
            request_id=request_id or None,
        )

    @app.exception_handler(TaskExecutionFailed)
    async def task_failed_handler(
        _: Request,
        exc: TaskExecutionFailed,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "detail": str(exc.cause),
                "task_id": exc.task_id,
                "trace_url": f"/api/v1/tasks/{exc.task_id}",
            },
        )

    @app.exception_handler(ModelDataPolicyError)
    async def model_data_policy_handler(
        _: Request,
        exc: ModelDataPolicyError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc)},
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "runtime": container.settings.runtime_name,
            "github_mode": "read_only",
            "github_auth": (
                "github_app"
                if container.github_connections.enabled
                else "token"
                if container.settings.github_token
                else "anonymous"
            ),
            "api_port": container.settings.api_port,
            "task_timeout_seconds": container.settings.worker_task_timeout_seconds,
            "queue": container.store.queue_summary(),
            "persona_identity_mode": "local_single_owner",
            "persona_blob_encryption": "AES-256-GCM",
            "persona_embedding_space_id": (
                container.memory_index.embedding_space_id
            ),
        }

    @app.post(
        "/api/v1/personas",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_persona(
        payload: PersonaCreate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.create(
                persona_access(request),
                display_name=payload.display_name,
                description=payload.description,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/personas")
    async def list_personas(
        request: Request,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        return container.personas.list(
            persona_access(request),
            include_inactive=include_inactive,
        )

    @app.get("/api/v1/personas/{persona_id}")
    async def get_persona(
        persona_id: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.get(
                persona_access(request),
                persona_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.patch("/api/v1/personas/{persona_id}/model-policy")
    async def update_persona_model_policy(
        persona_id: str,
        payload: PersonaModelPolicyUpdateRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.update_model_policy(
                persona_access(request),
                persona_id=persona_id,
                allowed_model_boundaries=payload.allowed_model_boundaries,
                external_data_acknowledged=(
                    payload.external_data_acknowledged
                ),
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/personas/{persona_id}/documents",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def upload_persona_document(
        persona_id: str,
        request: Request,
        file: Annotated[UploadFile, File()],
        language: str | None = Query(default=None, max_length=40),
    ) -> dict[str, Any]:
        try:
            content = await file.read(
                container.settings.persona_max_upload_bytes + 1
            )
            return container.personas.upload_text(
                persona_access(request),
                persona_id=persona_id,
                filename=file.filename or "",
                media_type=file.content_type,
                content=content,
                language=language,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        finally:
            await file.close()

    @app.get("/api/v1/personas/{persona_id}/documents")
    async def list_persona_documents(
        persona_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        try:
            return container.personas.list_documents(
                persona_access(request),
                persona_id=persona_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/documents/{document_id}")
    async def get_persona_document(
        document_id: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.get_document(
                persona_access(request),
                document_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.delete("/api/v1/documents/{document_id}")
    async def delete_persona_document(
        document_id: str,
        request: Request,
        confirm: bool = False,
    ) -> dict[str, Any]:
        try:
            return container.personas.delete_document(
                persona_access(request),
                document_id=document_id,
                confirmed=confirm,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/personas/{persona_id}/memory-candidates")
    async def list_persona_memory_candidates(
        persona_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        try:
            return container.personas.list_memories(
                persona_access(request),
                persona_id=persona_id,
                status="candidate",
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/personas/{persona_id}/memories")
    async def list_persona_memories(
        persona_id: str,
        request: Request,
        memory_status: str = Query(default="confirmed", alias="status"),
    ) -> list[dict[str, Any]]:
        try:
            return container.personas.list_memories(
                persona_access(request),
                persona_id=persona_id,
                status=memory_status,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/memories/{memory_id}")
    async def get_persona_memory(
        memory_id: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.get_memory(
                persona_access(request),
                memory_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.patch("/api/v1/memories/{memory_id}")
    async def update_persona_memory(
        memory_id: str,
        payload: PersonaMemoryUpdateRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.update_memory(
                persona_access(request),
                memory_id=memory_id,
                expected_version=payload.expected_version,
                content=payload.content,
                sensitivity=payload.sensitivity,
                reason=payload.reason,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.delete("/api/v1/memories/{memory_id}")
    async def delete_persona_memory(
        memory_id: str,
        request: Request,
        confirm: bool = False,
    ) -> dict[str, Any]:
        try:
            return container.personas.delete_memory(
                persona_access(request),
                memory_id=memory_id,
                confirmed=confirm,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/personas/{persona_id}/memory-relations",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_persona_memory_relation(
        persona_id: str,
        payload: PersonaMemoryRelationCreate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.create_memory_relation(
                persona_access(request),
                persona_id=persona_id,
                from_memory_id=payload.from_memory_id,
                to_memory_id=payload.to_memory_id,
                relation=payload.relation,
                confidence=payload.confidence,
                evidence_memory_version_ids=(
                    payload.evidence_memory_version_ids
                ),
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/memories/{memory_id}/relations")
    async def list_persona_memory_relations(
        memory_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        try:
            return container.personas.list_memory_relations(
                persona_access(request),
                memory_id=memory_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.delete("/api/v1/memory-relations/{relation_id}")
    async def delete_persona_memory_relation(
        relation_id: str,
        request: Request,
        confirm: bool = False,
    ) -> dict[str, Any]:
        try:
            return container.personas.delete_memory_relation(
                persona_access(request),
                relation_id=relation_id,
                confirmed=confirm,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @app.post("/api/v1/memory-candidates/{memory_id}/review")
    async def review_persona_memory(
        memory_id: str,
        payload: PersonaMemoryReviewRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.review_memory(
                persona_access(request),
                memory_id,
                action=payload.action,
                edited_content=payload.edited_content,
                reason=payload.reason,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/personas/{persona_id}/audit-events")
    async def list_persona_audit_events(
        persona_id: str,
        request: Request,
        limit: int = Query(default=200, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        try:
            return container.personas.list_audit_events(
                persona_access(request),
                persona_id=persona_id,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.post("/api/v1/personas/{persona_id}/export")
    async def export_persona(
        persona_id: str,
        payload: PersonaExportRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.export_persona(
                persona_access(request),
                persona_id=persona_id,
                include_raw_sources=payload.include_raw_sources,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/personas/{persona_id}/conversations",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_persona_conversation(
        persona_id: str,
        payload: PersonaConversationCreate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.persona_qa.create_conversation(
                persona_access(request),
                persona_id=persona_id,
                title=payload.title,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/conversations/{conversation_id}/messages")
    async def list_persona_conversation_messages(
        conversation_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        try:
            return container.persona_qa.list_messages(
                persona_access(request),
                conversation_id=conversation_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/conversations/{conversation_id}/messages",
        status_code=status.HTTP_201_CREATED,
    )
    async def ask_persona(
        conversation_id: str,
        payload: PersonaQuestionCreate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return await container.persona_qa.ask(
                persona_access(request),
                conversation_id=conversation_id,
                question=payload.content,
                top_k=payload.top_k,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except CitationValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="answer generator returned invalid citations",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/messages/{message_id}/citations")
    async def get_persona_answer_citations(
        message_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        try:
            return container.persona_qa.get_citations(
                persona_access(request),
                message_id=message_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/personas/{persona_id}/memories/reindex",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def reindex_persona_memories(
        persona_id: str,
        request: Request,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            max_length=200,
        ),
    ) -> dict[str, Any]:
        try:
            return container.memory_reindex.enqueue(
                persona_access(request),
                persona_id=persona_id,
                idempotency_key=idempotency_key,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/employees")
    async def list_employees() -> list[dict[str, Any]]:
        return container.store.list_employees()

    @app.get("/api/v1/skills")
    async def list_skills() -> list[dict[str, Any]]:
        return container.store.list_skills()

    @app.get("/api/v1/users/{user_id}/memory-sources")
    async def list_memory_sources(
        user_id: str,
        source_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return container.store.list_memory_sources(
            user_id=user_id,
            source_type=source_type,
            limit=limit,
        )

    @app.get("/api/v1/users/{user_id}/preferences")
    async def list_preferences(
        user_id: str,
        preference_status: str | None = Query(default=None, alias="status"),
        context: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            return container.store.list_preferences(
                user_id=user_id,
                status=preference_status,
                context=context,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.post("/api/v1/users/{user_id}/preferences/learn")
    async def learn_preferences(
        user_id: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return container.personalization.learn(
                user_id=user_id,
                task_id=task_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/preferences/{preference_id}")
    async def get_preference(
        preference_id: str,
        user_id: str = Query(min_length=1, max_length=64),
    ) -> dict[str, Any]:
        try:
            return container.store.get_preference_bundle(
                preference_id,
                user_id=user_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.post("/api/v1/preferences/{preference_id}/review")
    async def review_preference(
        preference_id: str,
        payload: PreferenceReviewRequest,
    ) -> dict[str, Any]:
        try:
            return container.store.review_preference(
                preference_id,
                user_id=payload.user_id,
                action=payload.action,
                reason=payload.reason,
                expires_at=payload.expires_at,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/runtime/status")
    async def runtime_status() -> dict[str, Any]:
        try:
            return await container.runtime.status()
        except HermesAdapterError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/github/connections",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_github_connection(
        payload: GitHubConnectionCreate,
    ) -> dict[str, Any]:
        try:
            return await container.github_connections.connect(
                user_id=payload.user_id,
                installation_id=payload.installation_id,
                repository=payload.repository,
            )
        except GitHubAppNotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except GitHubAdapterError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/github/connections")
    async def list_github_connections(
        user_id: str = Query(min_length=1, max_length=64),
        include_disconnected: bool = False,
    ) -> list[dict[str, Any]]:
        return container.github_connections.list(
            user_id=user_id,
            include_disconnected=include_disconnected,
        )

    @app.delete("/api/v1/github/connections/{connection_id}")
    async def disconnect_github_connection(
        connection_id: str,
        user_id: str = Query(min_length=1, max_length=64),
    ) -> dict[str, Any]:
        try:
            return container.github_connections.disconnect(
                connection_id,
                user_id=user_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/tasks")
    async def list_tasks(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        return container.store.list_tasks(limit=limit)

    @app.post(
        "/api/v1/tasks/project-maintenance",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_project_maintenance_task(
        payload: ProjectMaintenanceTaskCreate,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            max_length=200,
        ),
    ) -> dict[str, Any]:
        try:
            return container.project_maintenance.enqueue(
                ProjectMaintenanceCommand(
                    repository=payload.repository,
                    github_connection_id=payload.github_connection_id,
                    employee_id=payload.employee_id,
                    user_id=payload.user_id,
                    workflow_name=payload.workflow_name,
                    max_items=payload.max_items,
                ),
                idempotency_key=idempotency_key,
            )
        except TaskConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except GitHubAppNotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except (KeyError, PermissionError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/tasks/{task_id}/retry",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def retry_task(task_id: str) -> dict[str, Any]:
        try:
            queue_job = container.store.retry_failed_queue_job(task_id)
            bundle = container.store.get_task_bundle(task_id)
            bundle["queue_submission"] = {
                "created": False,
                "idempotency_replayed": False,
                "requeued": True,
                "queue_job_id": queue_job["id"],
            }
            return bundle
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/tasks/{task_id}/cancel",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def cancel_task(
        task_id: str,
        payload: TaskCancellationRequest,
    ) -> dict[str, Any]:
        try:
            cancellation = container.store.request_task_cancellation(
                task_id,
                requested_by=payload.requested_by,
                reason=payload.reason,
            )
            bundle = container.store.get_task_bundle(task_id)
            bundle["cancellation"] = cancellation
            return bundle
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        try:
            return container.store.get_task_bundle(task_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.post("/api/v1/approvals/{approval_id}/decision")
    async def decide_approval(
        approval_id: str,
        payload: ApprovalDecisionRequest,
    ) -> dict[str, Any]:
        try:
            return container.approvals.decide(
                approval_id,
                decision=payload.decision,
                edited_output=payload.edited_output,
                reason=payload.reason,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/tasks/{task_id}/feedback",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_feedback(
        task_id: str,
        payload: FeedbackCreate,
    ) -> dict[str, Any]:
        try:
            return container.personalization.add_feedback(
                task_id,
                comment=payload.comment,
                rating=payload.rating,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    return app


app = create_app()
