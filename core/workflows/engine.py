from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.workflows.models import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowStepDefinition,
)


@dataclass(slots=True)
class StepResult:
    output: Any
    pause: bool = False
    pause_reason: str | None = None


@dataclass(slots=True)
class WorkflowResult:
    status: str
    state: dict[str, Any]
    history: list[dict[str, Any]]
    pause_reason: str | None = None


StepHandler = Callable[
    [dict[str, Any], WorkflowStepDefinition],
    Awaitable[StepResult],
]
Checkpoint = Callable[
    [str, str | None, dict[str, Any], list[dict[str, Any]]],
    None,
]


class WorkflowExecutionError(RuntimeError):
    def __init__(self, step_id: str, cause: Exception) -> None:
        super().__init__(f"Workflow failed at step {step_id}: {cause}")
        self.step_id = step_id
        self.cause = cause


class WorkflowEngine:
    """Small code-driven state machine with retries, conditions and pause support."""

    def __init__(self, handlers: dict[str, StepHandler]) -> None:
        self._handlers = handlers

    async def run(
        self,
        definition: WorkflowDefinition,
        *,
        initial_state: dict[str, Any],
        checkpoint: Checkpoint,
    ) -> WorkflowResult:
        state = dict(initial_state)
        history: list[dict[str, Any]] = []

        for step in definition.steps:
            if not self._condition_matches(step.when, state):
                history.append(
                    {
                        "step_id": step.id,
                        "uses": step.uses,
                        "status": "skipped",
                        "attempts": 0,
                        "finished_at": datetime.now(UTC).isoformat(),
                    }
                )
                checkpoint("running", step.id, state, history)
                continue

            try:
                handler = self._handlers[step.uses]
            except KeyError as exc:
                error = ValueError(f"No workflow handler registered for {step.uses}")
                checkpoint("failed", step.id, state, history)
                raise WorkflowExecutionError(step.id, error) from exc

            checkpoint("running", step.id, state, history)
            started_at = datetime.now(UTC)
            attempts = 0
            while True:
                attempts += 1
                try:
                    result = await handler(state, step)
                    state[step.id] = result.output
                    history.append(
                        {
                            "step_id": step.id,
                            "uses": step.uses,
                            "status": "paused" if result.pause else "completed",
                            "attempts": attempts,
                            "started_at": started_at.isoformat(),
                            "finished_at": datetime.now(UTC).isoformat(),
                        }
                    )
                    if result.pause:
                        checkpoint("awaiting_approval", step.id, state, history)
                        return WorkflowResult(
                            status="awaiting_approval",
                            state=state,
                            history=history,
                            pause_reason=result.pause_reason,
                        )
                    checkpoint("running", step.id, state, history)
                    break
                except Exception as exc:
                    if attempts <= step.retries:
                        continue
                    history.append(
                        {
                            "step_id": step.id,
                            "uses": step.uses,
                            "status": "failed",
                            "attempts": attempts,
                            "started_at": started_at.isoformat(),
                            "finished_at": datetime.now(UTC).isoformat(),
                            "error": str(exc),
                        }
                    )
                    checkpoint("failed", step.id, state, history)
                    raise WorkflowExecutionError(step.id, exc) from exc

        checkpoint("completed", None, state, history)
        return WorkflowResult(status="completed", state=state, history=history)

    @staticmethod
    def _condition_matches(
        condition: WorkflowCondition | None,
        state: dict[str, Any],
    ) -> bool:
        if condition is None:
            return True
        value: Any = state
        for part in condition.path.split("."):
            if not isinstance(value, dict) or part not in value:
                return False
            value = value[part]
        return value == condition.equals
