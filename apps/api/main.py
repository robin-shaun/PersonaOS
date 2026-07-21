from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from apps.api.schemas import (
    ApprovalDecisionRequest,
    FeedbackCreate,
    ProjectMaintenanceTaskCreate,
)
from core.bootstrap import Container, build_container
from core.services.project_maintenance import (
    ProjectMaintenanceCommand,
    TaskExecutionFailed,
)


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()
    app = FastAPI(
        title="Digital Employee MVP",
        version="0.1.0",
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
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "runtime": container.settings.runtime_name,
            "github_mode": "read_only",
        }

    @app.get("/api/v1/employees")
    async def list_employees() -> list[dict[str, Any]]:
        return container.store.list_employees()

    @app.get("/api/v1/skills")
    async def list_skills() -> list[dict[str, Any]]:
        return container.store.list_skills()

    @app.get("/api/v1/tasks")
    async def list_tasks(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        return container.store.list_tasks(limit=limit)

    @app.post(
        "/api/v1/tasks/project-maintenance",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_project_maintenance_task(
        payload: ProjectMaintenanceTaskCreate,
    ) -> dict[str, Any]:
        try:
            return await container.project_maintenance.create_and_run(
                ProjectMaintenanceCommand(
                    repository=payload.repository,
                    employee_id=payload.employee_id,
                    user_id=payload.user_id,
                    workflow_name=payload.workflow_name,
                    max_items=payload.max_items,
                )
            )
        except (KeyError, PermissionError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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

