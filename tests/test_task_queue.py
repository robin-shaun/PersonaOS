from __future__ import annotations

import pytest

from core.bootstrap import Container
from core.services.project_maintenance import (
    ProjectMaintenanceCommand,
    TaskConflictError,
)
from core.storage.models import QueueJobRecord, utc_now


def test_idempotency_replays_only_the_same_request(container: Container) -> None:
    command = ProjectMaintenanceCommand(
        repository="example/project",
        user_id="shaun",
        max_items=20,
    )

    first = container.project_maintenance.enqueue(
        command,
        idempotency_key="daily-example-project",
    )
    replay = container.project_maintenance.enqueue(
        command,
        idempotency_key="daily-example-project",
    )

    assert replay["task"]["id"] == first["task"]["id"]
    assert first["queue_submission"]["created"] is True
    assert replay["queue_submission"]["idempotency_replayed"] is True

    with pytest.raises(TaskConflictError, match="different request"):
        container.project_maintenance.enqueue(
            ProjectMaintenanceCommand(
                repository="example/other-project",
                user_id="shaun",
                max_items=20,
            ),
            idempotency_key="daily-example-project",
        )


def test_failed_queue_job_can_be_retried_manually(container: Container) -> None:
    bundle = container.project_maintenance.enqueue(
        ProjectMaintenanceCommand(repository="example/project"),
        idempotency_key="queue-recovery",
    )
    task_id = bundle["task"]["id"]

    for attempt in range(1, 4):
        job = container.store.claim_next_queue_job(
            worker_id="failing-worker",
            lease_seconds=30,
        )
        assert job is not None
        assert job["attempts"] == attempt
        container.store.mark_task_failed(task_id, error="simulated failure")
        failed = container.store.fail_queue_job(
            job["id"],
            worker_id="failing-worker",
            error="simulated failure",
            retry_delay_seconds=0,
        )

    assert failed["status"] == "failed"
    assert container.store.get_task_bundle(task_id)["task"]["status"] == "failed"

    retried = container.store.retry_failed_queue_job(task_id)
    assert retried["status"] == "queued"
    assert retried["attempts"] == 0
    recovered_bundle = container.store.get_task_bundle(task_id)
    assert recovered_bundle["task"]["status"] == "pending"


def test_expired_final_lease_is_marked_failed(container: Container) -> None:
    bundle = container.project_maintenance.enqueue(
        ProjectMaintenanceCommand(repository="example/project"),
        idempotency_key="expired-final-lease",
    )
    task_id = bundle["task"]["id"]

    for _ in range(2):
        job = container.store.claim_next_queue_job(
            worker_id="crashing-worker",
            lease_seconds=30,
        )
        assert job is not None
        container.store.mark_task_failed(task_id, error="simulated failure")
        container.store.fail_queue_job(
            job["id"],
            worker_id="crashing-worker",
            error="simulated failure",
            retry_delay_seconds=0,
        )

    final_attempt = container.store.claim_next_queue_job(
        worker_id="crashing-worker",
        lease_seconds=30,
    )
    assert final_attempt is not None
    assert final_attempt["attempts"] == 3
    with container.database.session() as session:
        queue_job = session.get(QueueJobRecord, final_attempt["id"])
        assert queue_job is not None
        queue_job.lease_expires_at = utc_now()

    assert (
        container.store.claim_next_queue_job(
            worker_id="recovery-worker",
            lease_seconds=30,
        )
        is None
    )
    failed_bundle = container.store.get_task_bundle(task_id)
    assert failed_bundle["task"]["status"] == "failed"
    assert failed_bundle["queue_jobs"][0]["status"] == "failed"
    assert "final attempt" in failed_bundle["queue_jobs"][0]["last_error"]
