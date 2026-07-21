from __future__ import annotations

import asyncio

import pytest

from core.bootstrap import Container, build_container
from core.services.project_maintenance import (
    ProjectMaintenanceCommand,
    TaskConflictError,
)
from core.services.task_queue import TaskWorker
from core.storage.database import Database
from core.storage.models import QueueJobRecord, utc_now


class BlockingGitHubGateway:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled_calls = 0

    async def get_repository_snapshot(
        self,
        repository: str,
        *,
        max_items: int = 50,
    ):
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.cancelled_calls += 1


def container_with_blocking_github(
    container: Container,
    gateway: BlockingGitHubGateway,
) -> Container:
    return build_container(
        settings=container.settings,
        database=Database("sqlite://"),
        github=gateway,
    )


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


def test_queued_task_is_cancelled_immediately_and_idempotently(
    container: Container,
) -> None:
    bundle = container.project_maintenance.enqueue(
        ProjectMaintenanceCommand(repository="example/project"),
        idempotency_key="cancel-before-start",
    )
    task_id = bundle["task"]["id"]

    cancelled = container.store.request_task_cancellation(
        task_id,
        requested_by="shaun",
        reason="本次简报不再需要",
    )
    replay = container.store.request_task_cancellation(
        task_id,
        requested_by="shaun",
        reason="重复请求不应新增事件",
    )

    assert cancelled["status"] == "cancelled"
    assert cancelled["immediate"] is True
    assert replay["idempotent_replay"] is True
    assert (
        container.store.claim_next_queue_job(
            worker_id="idle-worker",
            lease_seconds=30,
        )
        is None
    )
    trace = container.store.get_task_bundle(task_id)
    assert trace["task"]["status"] == "cancelled"
    assert trace["queue_jobs"][0]["status"] == "cancelled"
    assert [event["event_type"] for event in trace["task_events"]] == [
        "cancellation_requested",
        "cancellation_completed",
    ]
    assert trace["task_events"][0]["detail"]["reason"] == "本次简报不再需要"
    with pytest.raises(ValueError, match="not in a retryable failed state"):
        container.store.retry_failed_queue_job(task_id)


def test_expired_lease_finishes_a_pending_cancellation(
    container: Container,
) -> None:
    bundle = container.project_maintenance.enqueue(
        ProjectMaintenanceCommand(repository="example/project"),
        idempotency_key="cancel-crashed-worker",
    )
    task_id = bundle["task"]["id"]
    leased = container.store.claim_next_queue_job(
        worker_id="crashed-worker",
        lease_seconds=30,
    )
    assert leased is not None
    container.store.request_task_cancellation(
        task_id,
        requested_by="shaun",
        reason="停止已经领取的任务",
    )
    with container.database.session() as session:
        queue_job = session.get(QueueJobRecord, leased["id"])
        assert queue_job is not None
        queue_job.lease_expires_at = utc_now()

    assert (
        container.store.claim_next_queue_job(
            worker_id="recovery-worker",
            lease_seconds=30,
        )
        is None
    )
    trace = container.store.get_task_bundle(task_id)
    assert trace["task"]["status"] == "cancelled"
    assert trace["queue_jobs"][0]["status"] == "cancelled"
    assert trace["task_events"][-1]["actor"] == "recovery-worker"


def test_cancellation_wins_when_timeout_finalization_races(
    container: Container,
) -> None:
    bundle = container.project_maintenance.enqueue(
        ProjectMaintenanceCommand(repository="example/project"),
        idempotency_key="cancel-timeout-race",
    )
    task_id = bundle["task"]["id"]
    leased = container.store.claim_next_queue_job(
        worker_id="racing-worker",
        lease_seconds=30,
    )
    assert leased is not None
    container.store.request_task_cancellation(
        task_id,
        requested_by="shaun",
        reason="取消应优先于超时",
    )

    finalized = container.store.timeout_queue_job(
        leased["id"],
        worker_id="racing-worker",
        timeout_seconds=1,
        retry_delay_seconds=0,
    )

    assert finalized["status"] == "cancelled"
    trace = container.store.get_task_bundle(task_id)
    assert trace["task"]["status"] == "cancelled"
    assert [event["event_type"] for event in trace["task_events"]] == [
        "cancellation_requested",
        "cancellation_completed",
    ]


@pytest.mark.asyncio
async def test_worker_cooperatively_cancels_a_running_task(
    container: Container,
) -> None:
    gateway = BlockingGitHubGateway()
    controlled = container_with_blocking_github(container, gateway)
    bundle = controlled.project_maintenance.enqueue(
        ProjectMaintenanceCommand(repository="example/project"),
        idempotency_key="cancel-running-task",
    )
    task_id = bundle["task"]["id"]
    worker = TaskWorker(
        store=controlled.store,
        project_maintenance=controlled.project_maintenance,
        worker_id="controlled-worker",
        lease_seconds=30,
        retry_delay_seconds=0,
        task_timeout_seconds=5,
        control_poll_interval_seconds=0.01,
    )

    worker_task = asyncio.create_task(worker.run_one())
    await asyncio.wait_for(gateway.started.wait(), timeout=1)
    requested = controlled.store.request_task_cancellation(
        task_id,
        requested_by="shaun",
        reason="用户主动停止",
    )
    result = await asyncio.wait_for(worker_task, timeout=1)

    assert requested["status"] == "cancelling"
    assert result is not None
    assert result["status"] == "cancelled"
    assert gateway.cancelled_calls == 1
    trace = controlled.store.get_task_bundle(task_id)
    assert trace["task"]["status"] == "cancelled"
    assert trace["queue_jobs"][0]["status"] == "cancelled"
    assert trace["runs"][0]["status"] == "cancelled"
    assert trace["workflow_runs"][0]["status"] == "cancelled"
    assert trace["task_events"][-1]["detail"]["reason"] == "用户主动停止"


@pytest.mark.asyncio
async def test_worker_timeout_retries_then_exhausts_attempts(
    container: Container,
) -> None:
    gateway = BlockingGitHubGateway()
    controlled = container_with_blocking_github(container, gateway)
    bundle = controlled.project_maintenance.enqueue(
        ProjectMaintenanceCommand(repository="example/project"),
        idempotency_key="timeout-task",
    )
    task_id = bundle["task"]["id"]
    worker = TaskWorker(
        store=controlled.store,
        project_maintenance=controlled.project_maintenance,
        worker_id="timeout-worker",
        lease_seconds=30,
        retry_delay_seconds=0,
        task_timeout_seconds=0.05,
        control_poll_interval_seconds=0.01,
    )

    results = [await worker.run_one() for _ in range(3)]

    assert [result["status"] for result in results if result is not None] == [
        "retry_scheduled",
        "retry_scheduled",
        "failed",
    ]
    assert all(result["timed_out"] for result in results if result is not None)
    trace = controlled.store.get_task_bundle(task_id)
    assert trace["task"]["status"] == "failed"
    assert trace["queue_jobs"][0]["status"] == "failed"
    assert [run["status"] for run in trace["runs"]] == [
        "timed_out",
        "timed_out",
        "timed_out",
    ]
    timeout_events = [
        event
        for event in trace["task_events"]
        if event["event_type"] == "execution_timed_out"
    ]
    assert len(timeout_events) == 3
    assert timeout_events[-1]["detail"]["retry_scheduled"] is False
    assert gateway.cancelled_calls == 3
