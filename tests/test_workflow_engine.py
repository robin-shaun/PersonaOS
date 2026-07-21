from __future__ import annotations

import pytest

from core.workflows.engine import StepResult, WorkflowEngine
from core.workflows.models import WorkflowDefinition


@pytest.mark.asyncio
async def test_workflow_retries_conditions_and_pause() -> None:
    attempts = 0
    checkpoints: list[str] = []

    async def unstable(state, step):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")
        return StepResult(output={"ready": True})

    async def should_not_run(state, step):
        raise AssertionError("condition should skip this step")

    async def approval(state, step):
        return StepResult(output={"approval_id": "a1"}, pause=True)

    workflow = WorkflowDefinition.model_validate(
        {
            "name": "test",
            "steps": [
                {"id": "prepare", "uses": "unstable", "retries": 1},
                {
                    "id": "skipped",
                    "uses": "skipped",
                    "when": {"path": "prepare.ready", "equals": False},
                },
                {"id": "approve", "uses": "approval"},
            ],
        }
    )
    engine = WorkflowEngine(
        {
            "unstable": unstable,
            "skipped": should_not_run,
            "approval": approval,
        }
    )

    result = await engine.run(
        workflow,
        initial_state={},
        checkpoint=lambda status, step, state, history: checkpoints.append(status),
    )

    assert attempts == 2
    assert result.status == "awaiting_approval"
    assert [item["status"] for item in result.history] == [
        "completed",
        "skipped",
        "paused",
    ]
    assert checkpoints[-1] == "awaiting_approval"

