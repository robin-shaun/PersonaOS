from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from core.services.project_maintenance import (
    ProjectMaintenanceService,
    TaskExecutionFailed,
)
from core.storage.repository import ExecutionStore


logger = logging.getLogger(__name__)


class TaskWorker:
    def __init__(
        self,
        *,
        store: ExecutionStore,
        project_maintenance: ProjectMaintenanceService,
        worker_id: str,
        lease_seconds: int = 300,
        retry_delay_seconds: float = 5.0,
    ) -> None:
        self._store = store
        self._project_maintenance = project_maintenance
        self.worker_id = worker_id
        self.lease_seconds = max(5, lease_seconds)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)

    async def run_one(self) -> dict[str, Any] | None:
        job = self._store.claim_next_queue_job(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None

        heartbeat = asyncio.create_task(self._heartbeat(job["id"]))
        try:
            try:
                bundle = await self._project_maintenance.run_task(job["task_id"])
            except Exception as exc:
                if not isinstance(exc, TaskExecutionFailed):
                    self._store.mark_task_failed(job["task_id"], error=str(exc))
                queue_job = self._store.fail_queue_job(
                    job["id"],
                    worker_id=self.worker_id,
                    error=str(exc),
                    retry_delay_seconds=self.retry_delay_seconds,
                )
                return {
                    "queue_job_id": job["id"],
                    "task_id": job["task_id"],
                    "status": (
                        "retry_scheduled"
                        if queue_job["status"] == "queued"
                        else "failed"
                    ),
                    "attempt": queue_job["attempts"],
                    "max_attempts": queue_job["max_attempts"],
                    "error": str(exc),
                }

            queue_job = self._store.complete_queue_job(
                job["id"],
                worker_id=self.worker_id,
            )
            return {
                "queue_job_id": job["id"],
                "task_id": job["task_id"],
                "status": "completed",
                "attempt": queue_job["attempts"],
                "task_status": bundle["task"]["status"],
                "recovered": bool(job.get("recovered")),
            }
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def run_forever(self, *, poll_interval_seconds: float = 1.0) -> None:
        poll_interval_seconds = max(0.05, poll_interval_seconds)
        while True:
            try:
                result = await self.run_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker loop failed before a job could be finalized")
                result = None
            if result is None:
                await asyncio.sleep(poll_interval_seconds)
            else:
                logger.info("Processed queue job", extra={"queue_result": result})

    async def _heartbeat(self, queue_job_id: str) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            extended = self._store.extend_queue_lease(
                queue_job_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            if not extended:
                return
