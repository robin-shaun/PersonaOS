from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from adapters.github.client import GitHubAdapterError
from apps.api.schemas import (
    ApprovalDecisionRequest,
    FeedbackCreate,
    GitHubConnectionCreate,
    ProjectMaintenanceTaskCreate,
    TaskCancellationRequest,
)
from core.bootstrap import Container, build_container
from core.services.github_connections import GitHubAppNotConfiguredError
from core.services.project_maintenance import (
    ProjectMaintenanceCommand,
    TaskConflictError,
    TaskExecutionFailed,
)


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()
    app = FastAPI(
        title="Digital Employee MVP",
        version="0.4.0",
        description=(
            "Approval-first, read-only GitHub project maintenance employee. "
            "Every result includes evidence and an execution trace."
        ),
    )
    app.state.container = container

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
        }

    @app.get("/api/v1/employees")
    async def list_employees() -> list[dict[str, Any]]:
        return container.store.list_employees()

    @app.get("/api/v1/skills")
    async def list_skills() -> list[dict[str, Any]]:
        return container.store.list_skills()

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
    ) -> dict[str, str]:
        try:
            feedback_id = container.store.add_feedback(
                task_id,
                comment=payload.comment,
                rating=payload.rating,
            )
            return {"feedback_id": feedback_id, "task_id": task_id}
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    return app


app = create_app()
