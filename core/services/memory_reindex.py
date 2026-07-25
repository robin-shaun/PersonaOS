from __future__ import annotations

from typing import Any

from core.retrieval.service import MemoryIndexService
from core.security.access import AccessContext
from core.services.project_maintenance import (
    TaskCancellationRequested,
    TaskExecutionFailed,
)
from core.storage.repository import ExecutionStore
from core.workflows.models import WorkflowCatalog

PERSONA_REINDEX_EMPLOYEE_ID = "persona-memory-curator-001"
PERSONA_REINDEX_WORKFLOW = "persona-memory-reindex"


class MemoryReindexService:
    def __init__(
        self,
        *,
        store: ExecutionStore,
        workflows: WorkflowCatalog,
        index: MemoryIndexService,
        queue_max_attempts: int,
    ) -> None:
        self._store = store
        self._workflows = workflows
        self._index = index
        self._queue_max_attempts = queue_max_attempts

    def enqueue(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        self._index.validate_persona_access(access, persona_id=persona_id)
        normalized_key = (idempotency_key or "").strip() or None
        submission = self._store.create_queued_task(
            employee_id=PERSONA_REINDEX_EMPLOYEE_ID,
            user_id=access.owner_id,
            workflow_name=PERSONA_REINDEX_WORKFLOW,
            task_input={
                "persona_id": persona_id,
                "embedding_space_id": self._index.embedding_space_id,
            },
            idempotency_key=(
                f"persona-memory-reindex:{persona_id}:{normalized_key}"
                if normalized_key is not None
                else None
            ),
            max_attempts=self._queue_max_attempts,
        )
        return {
            "task_id": submission["task_id"],
            "queue_job_id": submission["queue_job_id"],
            "created": submission["created"],
            "idempotency_replayed": not submission["created"],
            "embedding_space_id": self._index.embedding_space_id,
        }

    async def run_task(self, task_id: str) -> dict[str, Any]:
        task = self._store.get_task_for_execution(task_id)
        if task["status"] == "cancelling":
            raise TaskCancellationRequested(task_id)
        if task["workflow_name"] != PERSONA_REINDEX_WORKFLOW:
            raise ValueError(
                f"Memory reindex cannot run workflow {task['workflow_name']}"
            )
        if task["employee_id"] != PERSONA_REINDEX_EMPLOYEE_ID:
            raise PermissionError(
                "Memory reindex task is assigned to an unauthorized employee"
            )
        task_input = task["input"]
        if task_input["embedding_space_id"] != self._index.embedding_space_id:
            raise ValueError("Queued reindex task targets a different embedding space")
        workflow = self._workflows.get(PERSONA_REINDEX_WORKFLOW)
        plan = [
            {
                "step_id": step.id,
                "uses": step.uses,
                "retries": step.retries,
            }
            for step in workflow.steps
        ]
        task_run_id = self._store.start_task_run(task_id=task_id, plan=plan)
        initial_state = {
            "task": {
                "task_id": task_id,
                "task_run_id": task_run_id,
                "persona_id": task_input["persona_id"],
                "owner_id": task["user_id"],
            },
            "embedding_space_id": self._index.embedding_space_id,
        }
        workflow_run_id = self._store.create_workflow_run(
            task_run_id=task_run_id,
            workflow_name=workflow.name,
            workflow_version=workflow.version,
            initial_state=initial_state,
        )
        access = AccessContext(
            owner_id=str(task["user_id"]),
            actor_id="memory-reindex-worker",
            actor_type="system_worker",
            correlation_id=task_id,
        )
        try:
            output = self._index.reindex_persona(
                access,
                persona_id=str(task_input["persona_id"]),
                task_id=task_id,
            )
            history = [
                {
                    "step_id": workflow.steps[0].id,
                    "uses": workflow.steps[0].uses,
                    "status": "completed",
                    "attempt": 1,
                    "output": output,
                }
            ]
            self._store.checkpoint_workflow(
                workflow_run_id,
                status="completed",
                current_step=None,
                state={**initial_state, "reindex": output},
                history=history,
            )
            completed = self._store.mark_execution_completed(
                task_id=task_id,
                task_run_id=task_run_id,
                workflow_run_id=workflow_run_id,
                output=output,
            )
            if not completed:
                raise TaskCancellationRequested(task_id)
            return self._store.get_task_bundle(task_id)
        except TaskCancellationRequested:
            raise
        except Exception as exc:
            safe_error = f"{exc.__class__.__name__}: memory reindex failed"
            self._store.mark_execution_failed(
                task_id=task_id,
                task_run_id=task_run_id,
                workflow_run_id=workflow_run_id,
                error=safe_error,
            )
            raise TaskExecutionFailed(
                task_id,
                RuntimeError(safe_error),
            ) from exc
