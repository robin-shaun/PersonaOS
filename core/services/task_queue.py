from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from core.services.project_maintenance import (
    ProjectMaintenanceService,
    TaskCancellationRequested,
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
        task_timeout_seconds: float = 300.0,
        control_poll_interval_seconds: float = 0.25,
        task_handlers: dict[str, Any] | None = None,
    ) -> None:
        self._store = store
        self.worker_id = worker_id
        self.lease_seconds = max(5, lease_seconds)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.task_timeout_seconds = max(0.05, task_timeout_seconds)
        self.control_poll_interval_seconds = max(
            0.01, control_poll_interval_seconds
        )
        self._task_handlers = {
            "daily-project-maintenance": project_maintenance,
            **(task_handlers or {}),
        }

    async def run_one(self) -> dict[str, Any] | None:
        job = self._store.claim_next_queue_job(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None

        heartbeat = asyncio.create_task(self._heartbeat(job["id"]))
        try:
            if self._store.is_task_cancellation_requested(job["task_id"]):
                return self._finish_cancellation(job)
            return await self._run_claimed_job(job)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _run_claimed_job(self, job: dict[str, Any]) -> dict[str, Any]:
        try:
            task = self._store.get_task_for_execution(job["task_id"])
            handler = self._task_handlers[str(task["workflow_name"])]
        except Exception as exc:  # noqa: BLE001 - finalize every leased job
            return self._finish_failure(job, exc)
        processing = asyncio.create_task(
            handler.run_task(job["task_id"])
        )
        deadline = asyncio.get_running_loop().time() + self.task_timeout_seconds
        try:
            while not processing.done():
                if self._store.is_task_cancellation_requested(job["task_id"]):
                    processing.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await processing
                    return self._finish_cancellation(job)

                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    if processing.done():
                        break
                    cancellation_delivered = processing.cancel()
                    if not cancellation_delivered:
                        break
                    with suppress(asyncio.CancelledError, Exception):
                        await processing
                    return self._finish_timeout(job)

                await asyncio.wait(
                    {processing},
                    timeout=min(self.control_poll_interval_seconds, remaining),
                )

            try:
                bundle = processing.result()
            except TaskCancellationRequested:
                return self._finish_cancellation(job)
            except asyncio.CancelledError:
                if self._store.is_task_cancellation_requested(job["task_id"]):
                    return self._finish_cancellation(job)
                raise
            except Exception as exc:  # noqa: BLE001 - worker failure boundary
                if self._store.is_task_cancellation_requested(job["task_id"]):
                    return self._finish_cancellation(job)
                return self._finish_failure(job, exc)
            return self._finish_success(job, bundle)
        finally:
            if not processing.done():
                processing.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await processing

    def _finish_success(
        self,
        job: dict[str, Any],
        bundle: dict[str, Any],
    ) -> dict[str, Any]:
        queue_job = self._store.complete_queue_job(
            job["id"],
            worker_id=self.worker_id,
        )
        if queue_job["status"] == "cancelled":
            return self._cancellation_result(job, queue_job)
        return {
            "queue_job_id": job["id"],
            "task_id": job["task_id"],
            "status": "completed",
            "attempt": queue_job["attempts"],
            "task_status": bundle["task"]["status"],
            "recovered": bool(job.get("recovered")),
        }

    def _finish_failure(
        self,
        job: dict[str, Any],
        exc: Exception,
    ) -> dict[str, Any]:
        if not isinstance(exc, TaskExecutionFailed):
            self._store.mark_task_failed(job["task_id"], error=str(exc))
        queue_job = self._store.fail_queue_job(
            job["id"],
            worker_id=self.worker_id,
            error=str(exc),
            retry_delay_seconds=self.retry_delay_seconds,
        )
        if queue_job["status"] == "cancelled":
            return self._cancellation_result(job, queue_job)
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

    def _finish_cancellation(self, job: dict[str, Any]) -> dict[str, Any]:
        queue_job = self._store.complete_task_cancellation(
            job["id"],
            worker_id=self.worker_id,
        )
        return self._cancellation_result(job, queue_job)

    def _cancellation_result(
        self,
        job: dict[str, Any],
        queue_job: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "queue_job_id": job["id"],
            "task_id": job["task_id"],
            "status": "cancelled",
            "attempt": queue_job["attempts"],
            "task_status": "cancelled",
        }

    def _finish_timeout(self, job: dict[str, Any]) -> dict[str, Any]:
        queue_job = self._store.timeout_queue_job(
            job["id"],
            worker_id=self.worker_id,
            timeout_seconds=self.task_timeout_seconds,
            retry_delay_seconds=self.retry_delay_seconds,
        )
        if queue_job["status"] == "cancelled":
            return self._cancellation_result(job, queue_job)
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
            "timed_out": True,
            "error": queue_job["last_error"],
        }

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
