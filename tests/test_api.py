from __future__ import annotations

import httpx
import pytest

from apps.api.main import create_app
from core.bootstrap import Container
from core.services.task_queue import TaskWorker


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
        assert health.json()["api_port"] == 18110

        response = await client.post(
            "/api/v1/tasks/project-maintenance",
            headers={"Idempotency-Key": "api-project-maintenance-1"},
            json={
                "repository": "example/project",
                "user_id": "shaun",
                "max_items": 20,
            },
        )
        assert response.status_code == 202
        task = response.json()
        assert task["task"]["status"] == "pending"
        assert task["runs"] == []
        assert task["queue_jobs"][0]["status"] == "queued"
        assert task["queue_submission"]["created"] is True

        replay = await client.post(
            "/api/v1/tasks/project-maintenance",
            headers={"Idempotency-Key": "api-project-maintenance-1"},
            json={
                "repository": "example/project",
                "user_id": "shaun",
                "max_items": 20,
            },
        )
        assert replay.status_code == 202
        assert replay.json()["task"]["id"] == task["task"]["id"]
        assert replay.json()["queue_submission"]["idempotency_replayed"] is True

        worker = TaskWorker(
            store=container.store,
            project_maintenance=container.project_maintenance,
            worker_id="api-test-worker",
            lease_seconds=30,
            retry_delay_seconds=0,
        )
        worker_result = await worker.run_one()
        assert worker_result is not None
        assert worker_result["status"] == "completed"

        trace = await client.get(f"/api/v1/tasks/{task['task']['id']}")
        assert trace.status_code == 200
        task = trace.json()
        assert task["task"]["status"] == "awaiting_approval"
        assert task["queue_jobs"][0]["status"] == "completed"
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
