from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from adapters.github.client import normalize_repository
from adapters.github.models import GitHubGateway
from core.agents.employee import EmployeeCatalog, EmployeeDefinition
from core.evaluation.task_eval import ProjectMaintenanceEvaluator
from core.skills.executor import SkillExecutor
from core.storage.repository import ExecutionStore
from core.workflows.engine import StepResult, WorkflowEngine, WorkflowExecutionError
from core.workflows.models import (
    WorkflowCatalog,
    WorkflowDefinition,
    WorkflowStepDefinition,
)


@dataclass(frozen=True, slots=True)
class ProjectMaintenanceCommand:
    repository: str
    employee_id: str = "github-maintainer-001"
    user_id: str = "local-user"
    workflow_name: str = "daily-project-maintenance"
    max_items: int = 50


class TaskExecutionFailed(RuntimeError):
    def __init__(self, task_id: str, cause: Exception) -> None:
        super().__init__(f"Task {task_id} failed: {cause}")
        self.task_id = task_id
        self.cause = cause


class TaskConflictError(RuntimeError):
    pass


class TaskCancellationRequested(RuntimeError):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task {task_id} cancellation was requested")
        self.task_id = task_id


class ProjectMaintenanceService:
    def __init__(
        self,
        *,
        store: ExecutionStore,
        employees: EmployeeCatalog,
        workflows: WorkflowCatalog,
        skills: SkillExecutor,
        github: GitHubGateway,
        evaluator: ProjectMaintenanceEvaluator,
        queue_max_attempts: int = 3,
    ) -> None:
        self._store = store
        self._employees = employees
        self._workflows = workflows
        self._skills = skills
        self._github = github
        self._evaluator = evaluator
        self._queue_max_attempts = max(1, queue_max_attempts)

    def enqueue(
        self,
        command: ProjectMaintenanceCommand,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        repository, employee, workflow, task_input = self._resolve_command(command)
        try:
            submission = self._store.create_queued_task(
                employee_id=employee.employee_id,
                user_id=command.user_id,
                workflow_name=workflow.name,
                task_input=task_input,
                idempotency_key=idempotency_key,
                max_attempts=self._queue_max_attempts,
            )
        except ValueError as exc:
            if "Idempotency key" in str(exc):
                raise TaskConflictError(str(exc)) from exc
            raise
        bundle = self._store.get_task_bundle(submission["task_id"])
        bundle["queue_submission"] = {
            "created": submission["created"],
            "idempotency_replayed": not submission["created"],
            "queue_job_id": submission["queue_job_id"],
            "repository": repository,
        }
        return bundle

    async def create_and_run(
        self,
        command: ProjectMaintenanceCommand,
    ) -> dict[str, Any]:
        repository, employee, workflow, task_input = self._resolve_command(command)
        task_id = self._store.create_task(
            employee_id=employee.employee_id,
            user_id=command.user_id,
            workflow_name=workflow.name,
            task_input=task_input,
        )
        return await self._run_existing_task(
            task_id=task_id,
            repository=repository,
            employee=employee,
            workflow=workflow,
            user_id=command.user_id,
            max_items=command.max_items,
        )

    async def run_task(self, task_id: str) -> dict[str, Any]:
        task = self._store.get_task_for_execution(task_id)
        if task["status"] == "cancelling":
            raise TaskCancellationRequested(task_id)
        task_input = task["input"]
        command = ProjectMaintenanceCommand(
            repository=task_input["repository"],
            employee_id=task["employee_id"],
            user_id=task["user_id"],
            workflow_name=task["workflow_name"],
            max_items=int(task_input["max_items"]),
        )
        repository, employee, workflow, _ = self._resolve_command(command)
        return await self._run_existing_task(
            task_id=task_id,
            repository=repository,
            employee=employee,
            workflow=workflow,
            user_id=command.user_id,
            max_items=command.max_items,
        )

    def _resolve_command(
        self,
        command: ProjectMaintenanceCommand,
    ) -> tuple[
        str,
        EmployeeDefinition,
        WorkflowDefinition,
        dict[str, Any],
    ]:
        repository = normalize_repository(command.repository)
        if not 1 <= command.max_items <= 100:
            raise ValueError("max_items must be between 1 and 100")
        employee = self._employees.get(command.employee_id)
        workflow = self._workflows.get(command.workflow_name)
        self._validate_assignment(employee, workflow)
        return (
            repository,
            employee,
            workflow,
            {
                "repository": repository,
                "max_items": command.max_items,
                "read_only": True,
            },
        )

    async def _run_existing_task(
        self,
        *,
        task_id: str,
        repository: str,
        employee: EmployeeDefinition,
        workflow: WorkflowDefinition,
        user_id: str,
        max_items: int,
    ) -> dict[str, Any]:
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
                "repository": repository,
                "max_items": max_items,
                "employee_id": employee.employee_id,
                "user_id": user_id,
            }
        }
        workflow_run_id = self._store.create_workflow_run(
            task_run_id=task_run_id,
            workflow_name=workflow.name,
            workflow_version=workflow.version,
            initial_state=initial_state,
        )

        handlers = self._handlers(
            employee=employee,
            task_id=task_id,
            task_run_id=task_run_id,
            repository=repository,
            max_items=max_items,
        )
        engine = WorkflowEngine(handlers)

        def checkpoint(
            status: str,
            current_step: str | None,
            state: dict[str, Any],
            history: list[dict[str, Any]],
        ) -> None:
            self._store.checkpoint_workflow(
                workflow_run_id,
                status=status,
                current_step=current_step,
                state=state,
                history=history,
            )

        try:
            result = await engine.run(
                workflow,
                initial_state=initial_state,
                checkpoint=checkpoint,
            )
            if result.status != "awaiting_approval":
                raise RuntimeError(
                    "MVP workflow must pause at the delivery approval gate"
                )
            proposed_output = result.state["delivery_approval"]["proposed_output"]
            waiting_recorded = self._store.mark_execution_waiting(
                task_id=task_id,
                task_run_id=task_run_id,
                output=proposed_output,
            )
            if not waiting_recorded:
                raise TaskCancellationRequested(task_id)
            return self._store.get_task_bundle(task_id)
        except TaskCancellationRequested:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._store.is_task_cancellation_requested(task_id):
                raise TaskCancellationRequested(task_id) from exc
            failure_recorded = self._store.mark_execution_failed(
                task_id=task_id,
                task_run_id=task_run_id,
                workflow_run_id=workflow_run_id,
                error=str(exc),
            )
            if not failure_recorded:
                raise TaskCancellationRequested(task_id) from exc
            cause = exc.cause if isinstance(exc, WorkflowExecutionError) else exc
            raise TaskExecutionFailed(task_id, cause) from exc

    def _handlers(
        self,
        *,
        employee: EmployeeDefinition,
        task_id: str,
        task_run_id: str,
        repository: str,
        max_items: int,
    ) -> dict[str, Any]:
        async def collect_repository(
            _: dict[str, Any],
            __: WorkflowStepDefinition,
        ) -> StepResult:
            started = perf_counter()
            tool_input = {
                "repository": repository,
                "max_items": max_items,
                "operation": "read",
            }
            try:
                snapshot = await self._github.get_repository_snapshot(
                    repository,
                    max_items=max_items,
                )
                payload = snapshot.model_dump(mode="json")
                self._store.record_tool_call(
                    task_run_id=task_run_id,
                    tool_name="github_repository_reader",
                    tool_input=tool_input,
                    status="completed",
                    output={
                        "repository": repository,
                        "sampled_issue_count": len(snapshot.issues),
                        "sampled_pull_request_count": len(snapshot.pull_requests),
                        "fetched_at": payload["fetched_at"],
                    },
                    error=None,
                    latency_ms=int((perf_counter() - started) * 1000),
                )
                return StepResult(output=payload)
            except Exception as exc:
                self._store.record_tool_call(
                    task_run_id=task_run_id,
                    tool_name="github_repository_reader",
                    tool_input=tool_input,
                    status="failed",
                    output=None,
                    error=str(exc),
                    latency_ms=int((perf_counter() - started) * 1000),
                )
                raise

        def skill_handler(skill_name: str) -> Any:
            async def execute_skill(
                state: dict[str, Any],
                _: WorkflowStepDefinition,
            ) -> StepResult:
                result = await self._skills.execute(
                    skill_name,
                    employee=employee,
                    context={
                        "repository_snapshot": state["collect_repository"],
                    },
                )
                return StepResult(
                    output={
                        "content": result.output,
                        "execution": {
                            "runtime": result.runtime,
                            "usage": result.usage,
                            "metadata": result.metadata,
                        },
                    }
                )

            return execute_skill

        async def evaluate(
            state: dict[str, Any],
            _: WorkflowStepDefinition,
        ) -> StepResult:
            report = self._evaluator.evaluate(
                repository_snapshot=state["collect_repository"],
                brief=state["daily_brief"]["content"],
                triage=state["issue_triage"]["content"],
            )
            if not report["passed"]:
                raise ValueError(
                    f"Quality gate failed with score {report['score']}"
                )
            return StepResult(output=report)

        async def request_approval(
            state: dict[str, Any],
            _: WorkflowStepDefinition,
        ) -> StepResult:
            policy = employee.approval_policy.get(
                "deliver_recommendation", "required"
            )
            if policy == "forbidden":
                raise PermissionError("Employee is forbidden from delivering results")
            if policy != "required":
                raise ValueError(
                    "The MVP requires deliver_recommendation to use human approval"
                )
            proposed_output = {
                "repository": repository,
                "brief": state["daily_brief"]["content"],
                "issue_triage": state["issue_triage"]["content"],
                "evaluation": state["quality_evaluation"],
                "execution": {
                    "employee_id": employee.employee_id,
                    "workflow": "daily-project-maintenance",
                    "runtimes": {
                        "daily_brief": state["daily_brief"]["execution"],
                        "issue_triage": state["issue_triage"]["execution"],
                    },
                    "read_only": True,
                    "github_mutations_performed": 0,
                },
            }
            artifact_id = self._store.create_artifact(
                task_id=task_id,
                task_run_id=task_run_id,
                artifact_type="project_maintenance_report",
                content=proposed_output,
                version=1,
            )
            approval_id = self._store.create_approval(
                task_id=task_id,
                task_run_id=task_run_id,
                action="deliver_recommendation",
                proposed_output=proposed_output,
            )
            return StepResult(
                output={
                    "artifact_id": artifact_id,
                    "approval_id": approval_id,
                    "proposed_output": proposed_output,
                },
                pause=True,
                pause_reason="deliver_recommendation requires user approval",
            )

        return {
            "tool.github_repository_reader": collect_repository,
            "skill.project-daily-brief": skill_handler("project-daily-brief"),
            "skill.issue-triage": skill_handler("issue-triage"),
            "evaluation.project-maintenance": evaluate,
            "approval.deliver_recommendation": request_approval,
        }

    @staticmethod
    def _validate_assignment(
        employee: EmployeeDefinition,
        workflow: WorkflowDefinition,
    ) -> None:
        if workflow.name not in employee.workflows:
            raise PermissionError(
                f"Employee {employee.employee_id} cannot run workflow {workflow.name}"
            )
        for step in workflow.steps:
            if step.uses.startswith("skill."):
                skill_name = step.uses.removeprefix("skill.")
                if skill_name not in employee.skills:
                    raise PermissionError(
                        f"Employee {employee.employee_id} cannot use skill {skill_name}"
                    )


class ApprovalService:
    def __init__(self, store: ExecutionStore) -> None:
        self._store = store

    def decide(
        self,
        approval_id: str,
        *,
        decision: str,
        edited_output: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        approval = self._store.resolve_approval(
            approval_id,
            decision=decision,
            edited_output=edited_output,
            reason=reason,
        )
        return self._store.get_task_bundle(approval["task_id"])
