from __future__ import annotations

import httpx
import pytest

from apps.api.main import create_app
from core.bootstrap import Container


@pytest.mark.asyncio
async def test_api_runs_task_and_accepts_approval(container: Container) -> None:
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["github_mode"] == "read_only"

        response = await client.post(
            "/api/v1/tasks/project-maintenance",
            json={
                "repository": "example/project",
                "user_id": "shaun",
                "max_items": 20,
            },
        )
        assert response.status_code == 201
        task = response.json()
        approval_id = task["approvals"][0]["id"]

        approved = await client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={
                "decision": "approved",
                "reason": "建议可直接采用",
            },
        )
        assert approved.status_code == 200
        assert approved.json()["task"]["status"] == "completed"

        trace = await client.get(f"/api/v1/tasks/{task['task']['id']}")
        assert trace.status_code == 200
        assert len(trace.json()["decision_records"]) == 1

