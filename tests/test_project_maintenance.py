from __future__ import annotations

from copy import deepcopy

import pytest

from core.bootstrap import Container
from core.services.project_maintenance import ProjectMaintenanceCommand


@pytest.mark.asyncio
async def test_task_waits_for_approval_with_a_complete_trace(
    container: Container,
) -> None:
    bundle = await container.project_maintenance.create_and_run(
        ProjectMaintenanceCommand(
            repository="example/project",
            user_id="shaun",
            max_items=25,
        )
    )

    assert bundle["task"]["status"] == "awaiting_approval"
    assert bundle["task"]["input"]["read_only"] is True
    assert len(bundle["runs"]) == 1
    assert bundle["runs"][0]["status"] == "awaiting_approval"
    assert len(bundle["tool_calls"]) == 1
    assert bundle["tool_calls"][0]["tool_name"] == "github_repository_reader"
    assert bundle["tool_calls"][0]["status"] == "completed"
    assert len(bundle["approvals"]) == 1
    assert bundle["approvals"][0]["status"] == "pending"
    assert len(bundle["artifacts"]) == 1
    assert bundle["artifacts"][0]["version"] == 1

    report = bundle["approvals"][0]["proposed_output"]
    assert report["evaluation"]["passed"] is True
    assert report["evaluation"]["score"] == 100.0
    assert report["execution"]["github_mutations_performed"] == 0
    triage = report["issue_triage"]["recommendations"]
    assert [item["issue_number"] for item in triage] == [10, 11, 12]
    assert triage[0]["priority"] == "P0"

    workflow = bundle["workflow_runs"][0]
    assert workflow["status"] == "awaiting_approval"
    assert [item["status"] for item in workflow["history"]] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "paused",
    ]


@pytest.mark.asyncio
async def test_user_edit_is_preserved_as_feedback_and_decision_evidence(
    container: Container,
) -> None:
    bundle = await container.project_maintenance.create_and_run(
        ProjectMaintenanceCommand(repository="example/project", user_id="shaun")
    )
    approval = bundle["approvals"][0]
    edited = deepcopy(approval["proposed_output"])
    edited["brief"]["summary"] = "结论前置：先处理安全问题，再审阅开放 PR。"

    decided = container.approvals.decide(
        approval["id"],
        decision="approved_with_edits",
        edited_output=edited,
        reason="把处置结论移动到开头",
    )

    assert decided["task"]["status"] == "completed"
    assert decided["task"]["final_output"] == edited
    assert decided["approvals"][0]["status"] == "approved_with_edits"
    assert [item["version"] for item in decided["artifacts"]] == [1, 2]
    assert decided["artifacts"][0]["content"] == approval["proposed_output"]
    assert decided["artifacts"][1]["content"] == edited
    assert decided["feedback"][0]["kind"] == "user_edit"
    assert decided["feedback"][0]["original"] == approval["proposed_output"]
    assert decided["feedback"][0]["revised"] == edited
    assert decided["decision_records"][0]["user_choice"] == "approved_with_edits"
    assert decided["decision_records"][0]["user_reason"] == "把处置结论移动到开头"


@pytest.mark.asyncio
async def test_rejection_keeps_the_proposal_but_does_not_deliver_it(
    container: Container,
) -> None:
    bundle = await container.project_maintenance.create_and_run(
        ProjectMaintenanceCommand(repository="example/project")
    )
    approval_id = bundle["approvals"][0]["id"]

    decided = container.approvals.decide(
        approval_id,
        decision="rejected",
        reason="排序不符合本周目标",
    )

    assert decided["task"]["status"] == "rejected"
    assert decided["task"]["final_output"] is None
    assert decided["approvals"][0]["proposed_output"]
    assert decided["feedback"][0]["kind"] == "rejection"
    assert decided["decision_records"][0]["final_outcome"] == {"status": "rejected"}

