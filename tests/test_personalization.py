from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from apps.api.main import create_app
from core.bootstrap import Container
from core.identity.preference import PreferenceEvidenceExtractor, json_changes
from core.services.project_maintenance import ProjectMaintenanceCommand
from core.storage.models import PreferenceRecord


def test_json_changes_preserves_structural_edit_evidence() -> None:
    changes = json_changes(
        {
            "summary": "背景在前",
            "risks": ["成本"],
            "obsolete": True,
        },
        {
            "summary": "结论在前",
            "risks": ["成本", "延期"],
            "metric": "审批通过率",
        },
    )

    assert changes == [
        {
            "path": "metric",
            "operation": "add",
            "after": "审批通过率",
        },
        {
            "path": "obsolete",
            "operation": "remove",
            "before": True,
        },
        {
            "path": "risks",
            "operation": "replace",
            "before": ["成本"],
            "after": ["成本", "延期"],
        },
        {
            "path": "summary",
            "operation": "replace",
            "before": "背景在前",
            "after": "结论在前",
        },
    ]


def test_unexplained_user_edit_stays_a_low_confidence_candidate() -> None:
    evidence = PreferenceEvidenceExtractor().from_feedback(
        task={
            "id": "task-1",
            "user_id": "shaun",
            "workflow_name": "daily-project-maintenance",
        },
        feedback={
            "id": "feedback-1",
            "kind": "user_edit",
            "comment": None,
            "rating": None,
            "original": {"brief": {"summary": "背景在前"}},
            "revised": {"brief": {"summary": "结论在前"}},
            "created_at": "2026-07-24T08:00:00+00:00",
        },
    )

    assert evidence.candidate is not None
    assert evidence.candidate.weight == 0.45
    assert evidence.candidate.extraction_method == "user-edit-diff-v1"
    assert "brief.summary（replace）" in evidence.candidate.rule


@pytest.mark.asyncio
async def test_user_edit_becomes_reviewable_preference_and_confirmed_context(
    container: Container,
) -> None:
    bundle = await container.project_maintenance.create_and_run(
        ProjectMaintenanceCommand(
            repository="example/project",
            user_id="shaun",
        )
    )
    approval = bundle["approvals"][0]
    edited = deepcopy(approval["proposed_output"])
    edited["brief"]["summary"] = "结论前置：先处理安全问题，再审阅开放 PR。"

    decided = container.approvals.decide(
        approval["id"],
        decision="approved_with_edits",
        edited_output=edited,
        reason="产品方案先给结论，并保留可验证指标",
    )

    learning = decided["preference_learning"]
    assert learning["sources_created"] == 2
    assert learning["candidates_created"] == 1
    assert learning["evidence_links_created"] == 1
    assert {item["source_type"] for item in decided["memory_sources"]} == {
        "feedback",
        "decision_record",
    }

    candidate = decided["preference_candidates"][0]
    assert candidate["status"] == "candidate"
    assert candidate["effective_status"] == "candidate"
    assert candidate["context"] == "daily-project-maintenance"
    assert candidate["category"] == "work_product"
    assert candidate["rule"] == "产品方案先给结论，并保留可验证指标"
    assert candidate["confidence"] == 0.75
    assert candidate["evidence_count"] == 1

    detail = container.store.get_preference_bundle(
        candidate["id"],
        user_id="shaun",
    )
    assert detail["evidence"][0]["source"]["source_kind"] == "user_edit"
    changed_paths = {
        item["path"]
        for item in detail["evidence"][0]["source"]["content"]["changes"]
    }
    assert "brief.summary" in changed_paths

    replay = container.personalization.learn_from_task(bundle["task"]["id"])
    assert replay["sources_created"] == 0
    assert replay["candidates_created"] == 0
    assert replay["evidence_links_created"] == 0

    reviewed = container.store.review_preference(
        candidate["id"],
        user_id="shaun",
        action="confirm",
        reason="这条规则可以用于后续项目维护工作",
        expires_at=None,
    )
    assert reviewed["preference"]["status"] == "confirmed"
    assert reviewed["reviews"][0]["previous_status"] == "candidate"
    assert reviewed["reviews"][0]["new_status"] == "confirmed"

    next_bundle = await container.project_maintenance.create_and_run(
        ProjectMaintenanceCommand(
            repository="example/project",
            user_id="shaun",
        )
    )
    personal_context = next_bundle["workflow_runs"][0]["state"][
        "personalization"
    ]
    assert personal_context["policy"]["confirmed_only"] is True
    assert personal_context["preferences"] == [
        {
            "preference_id": candidate["id"],
            "context": "daily-project-maintenance",
            "category": "work_product",
            "rule": "产品方案先给结论，并保留可验证指标",
            "confidence": 0.75,
            "evidence_count": 1,
        }
    ]
    report = next_bundle["approvals"][0]["proposed_output"]
    assert report["execution"]["personalization"][
        "applied_preference_ids"
    ] == [candidate["id"]]


@pytest.mark.asyncio
async def test_preference_api_enforces_owner_and_review_lifecycle(
    container: Container,
) -> None:
    bundle = await container.project_maintenance.create_and_run(
        ProjectMaintenanceCommand(
            repository="example/project",
            user_id="shaun",
        )
    )
    container.personalization.add_feedback(
        bundle["task"]["id"],
        comment="Issue 建议必须说明优先级依据",
        rating=4,
    )
    candidate = container.store.list_preferences(user_id="shaun")[0]

    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        listed = await client.get("/api/v1/users/shaun/preferences")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == candidate["id"]

        sources = await client.get("/api/v1/users/shaun/memory-sources")
        assert sources.status_code == 200
        assert sources.json()[0]["source_type"] == "feedback"

        hidden = await client.get(
            f"/api/v1/preferences/{candidate['id']}",
            params={"user_id": "another-user"},
        )
        assert hidden.status_code == 404

        confirmed = await client.post(
            f"/api/v1/preferences/{candidate['id']}/review",
            json={
                "user_id": "shaun",
                "action": "confirm",
                "reason": "确认使用",
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["preference"]["status"] == "confirmed"

        cannot_reject = await client.post(
            f"/api/v1/preferences/{candidate['id']}/review",
            json={
                "user_id": "shaun",
                "action": "reject",
            },
        )
        assert cannot_reject.status_code == 409

        with container.database.session() as session:
            stored = session.get(PreferenceRecord, candidate["id"])
            assert stored is not None
            stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        expired = await client.get(
            f"/api/v1/preferences/{candidate['id']}",
            params={"user_id": "shaun"},
        )
        assert expired.json()["preference"]["effective_status"] == "expired"
        assert container.personalization.for_task(
            user_id="shaun",
            context="daily-project-maintenance",
        ).preferences == []

        revoked = await client.post(
            f"/api/v1/preferences/{candidate['id']}/review",
            json={
                "user_id": "shaun",
                "action": "revoke",
                "reason": "暂时不再应用",
            },
        )
        assert revoked.status_code == 200
        assert revoked.json()["preference"]["status"] == "revoked"
