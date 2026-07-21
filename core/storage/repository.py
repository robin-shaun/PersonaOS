from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.agents.employee import EmployeeDefinition
from core.skills.models import SkillDefinition
from core.storage.database import Database
from core.storage.models import (
    ApprovalRecord,
    ArtifactRecord,
    DecisionRecord,
    EmployeeAssignmentRecord,
    EmployeeRecord,
    FeedbackRecord,
    QueueJobRecord,
    SkillRecord,
    SkillVersionRecord,
    TaskEventRecord,
    TaskRecord,
    TaskRunRecord,
    ToolCallRecord,
    UserRecord,
    WorkflowRecord,
    WorkflowRunRecord,
    utc_now,
)
from core.workflows.models import WorkflowDefinition


def _new_id() -> str:
    return str(uuid4())


def _record_dict(record: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in record.__table__.columns:
        value = getattr(record, column.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        result[column.name] = value
    return result


def _elapsed_ms(started_at: datetime, finished_at: datetime) -> int:
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return max(0, int((finished_at - started_at).total_seconds() * 1000))


class ExecutionStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def seed_definitions(
        self,
        *,
        employees: list[EmployeeDefinition],
        skills: list[SkillDefinition],
        workflows: list[WorkflowDefinition],
    ) -> None:
        with self.database.session() as session:
            for employee_definition in employees:
                employee_record = session.get(
                    EmployeeRecord, employee_definition.employee_id
                )
                payload = employee_definition.model_dump(mode="json")
                if employee_record is None:
                    session.add(
                        EmployeeRecord(
                            id=employee_definition.employee_id,
                            name=employee_definition.name,
                            role=employee_definition.role,
                            definition=payload,
                        )
                    )
                else:
                    employee_record.name = employee_definition.name
                    employee_record.role = employee_definition.role
                    employee_record.definition = payload

            for skill_definition in skills:
                skill = session.get(SkillRecord, skill_definition.name)
                if skill is None:
                    session.add(
                        SkillRecord(
                            id=skill_definition.name,
                            description=skill_definition.description,
                        )
                    )
                    session.flush()
                else:
                    skill.description = skill_definition.description
                version = session.scalar(
                    select(SkillVersionRecord).where(
                        SkillVersionRecord.skill_id == skill_definition.name,
                        SkillVersionRecord.version == skill_definition.version,
                    )
                )
                payload = skill_definition.model_dump(mode="json")
                if version is None:
                    session.add(
                        SkillVersionRecord(
                            id=_new_id(),
                            skill_id=skill_definition.name,
                            version=skill_definition.version,
                            definition=payload,
                        )
                    )
                else:
                    version.definition = payload

            for workflow_definition in workflows:
                workflow_record = session.scalar(
                    select(WorkflowRecord).where(
                        WorkflowRecord.name == workflow_definition.name,
                        WorkflowRecord.version == workflow_definition.version,
                    )
                )
                payload = workflow_definition.model_dump(mode="json")
                if workflow_record is None:
                    session.add(
                        WorkflowRecord(
                            id=_new_id(),
                            name=workflow_definition.name,
                            version=workflow_definition.version,
                            definition=payload,
                        )
                    )
                else:
                    workflow_record.definition = payload

    def create_task(
        self,
        *,
        employee_id: str,
        user_id: str,
        workflow_name: str,
        task_input: dict[str, Any],
    ) -> str:
        task_id = _new_id()
        with self.database.session() as session:
            self._ensure_user(session, user_id)
            if session.get(EmployeeRecord, employee_id) is None:
                raise KeyError(f"Unknown employee: {employee_id}")
            assignment = session.scalar(
                select(EmployeeAssignmentRecord).where(
                    EmployeeAssignmentRecord.user_id == user_id,
                    EmployeeAssignmentRecord.employee_id == employee_id,
                )
            )
            if assignment is None:
                session.add(
                    EmployeeAssignmentRecord(
                        id=_new_id(),
                        user_id=user_id,
                        employee_id=employee_id,
                    )
                )
            session.add(
                TaskRecord(
                    id=task_id,
                    employee_id=employee_id,
                    user_id=user_id,
                    workflow_name=workflow_name,
                    status="pending",
                    input=task_input,
                )
            )
        return task_id

    def create_queued_task(
        self,
        *,
        employee_id: str,
        user_id: str,
        workflow_name: str,
        task_input: dict[str, Any],
        idempotency_key: str | None,
        max_attempts: int,
    ) -> dict[str, Any]:
        normalized_key = (idempotency_key or "").strip() or None
        task_id = _new_id()
        queue_job_id = _new_id()
        try:
            with self.database.session() as session:
                existing = self._queue_job_for_key(
                    session,
                    user_id=user_id,
                    idempotency_key=normalized_key,
                )
                if existing is not None:
                    task = self._required(session, TaskRecord, existing.task_id)
                    self._assert_idempotent_request_matches(
                        task,
                        employee_id=employee_id,
                        workflow_name=workflow_name,
                        task_input=task_input,
                    )
                    return {
                        "task_id": task.id,
                        "queue_job_id": existing.id,
                        "created": False,
                    }

                self._ensure_user(session, user_id)
                if session.get(EmployeeRecord, employee_id) is None:
                    raise KeyError(f"Unknown employee: {employee_id}")
                assignment = session.scalar(
                    select(EmployeeAssignmentRecord).where(
                        EmployeeAssignmentRecord.user_id == user_id,
                        EmployeeAssignmentRecord.employee_id == employee_id,
                    )
                )
                if assignment is None:
                    session.add(
                        EmployeeAssignmentRecord(
                            id=_new_id(),
                            user_id=user_id,
                            employee_id=employee_id,
                        )
                    )
                session.add(
                    TaskRecord(
                        id=task_id,
                        employee_id=employee_id,
                        user_id=user_id,
                        workflow_name=workflow_name,
                        status="pending",
                        input=task_input,
                    )
                )
                session.flush()
                session.add(
                    QueueJobRecord(
                        id=queue_job_id,
                        task_id=task_id,
                        user_id=user_id,
                        status="queued",
                        idempotency_key=normalized_key,
                        max_attempts=max(1, max_attempts),
                    )
                )
        except IntegrityError:
            if normalized_key is None:
                raise
            with self.database.session() as session:
                existing = self._queue_job_for_key(
                    session,
                    user_id=user_id,
                    idempotency_key=normalized_key,
                )
                if existing is None:
                    raise
                task = self._required(session, TaskRecord, existing.task_id)
                self._assert_idempotent_request_matches(
                    task,
                    employee_id=employee_id,
                    workflow_name=workflow_name,
                    task_input=task_input,
                )
                return {
                    "task_id": task.id,
                    "queue_job_id": existing.id,
                    "created": False,
                }
        return {
            "task_id": task_id,
            "queue_job_id": queue_job_id,
            "created": True,
        }

    def get_task_for_execution(self, task_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            task = self._required(session, TaskRecord, task_id)
            return _record_dict(task)

    def request_task_cancellation(
        self,
        task_id: str,
        *,
        requested_by: str,
        reason: str,
    ) -> dict[str, Any]:
        now = utc_now()
        normalized_actor = requested_by.strip() or "api"
        normalized_reason = reason.strip() or "cancelled by user"
        with self.database.session() as session:
            task = self._required(session, TaskRecord, task_id)
            job = session.scalar(
                select(QueueJobRecord).where(QueueJobRecord.task_id == task_id)
            )
            if job is None:
                raise ValueError(f"Task {task_id} was not created through the queue")

            if task.status == "cancelled" and job.status == "cancelled":
                return {
                    "task_id": task_id,
                    "queue_job_id": job.id,
                    "status": "cancelled",
                    "immediate": True,
                    "idempotent_replay": True,
                }
            if task.status == "cancelling" and job.status == "leased":
                return {
                    "task_id": task_id,
                    "queue_job_id": job.id,
                    "status": "cancelling",
                    "immediate": False,
                    "idempotent_replay": True,
                }
            if task.status not in {"pending", "running"}:
                raise ValueError(
                    f"Task {task_id} cannot be cancelled from status {task.status}"
                )

            if job.status == "queued":
                result = session.execute(
                    update(QueueJobRecord)
                    .where(
                        QueueJobRecord.id == job.id,
                        QueueJobRecord.status == "queued",
                    )
                    .values(
                        status="cancelled",
                        lease_owner=None,
                        lease_expires_at=None,
                        last_error=normalized_reason,
                        finished_at=now,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount == 1:
                    self._add_task_event(
                        session,
                        task_id=task_id,
                        event_type="cancellation_requested",
                        actor=normalized_actor,
                        detail={
                            "reason": normalized_reason,
                            "queue_status": "queued",
                        },
                        created_at=now,
                    )
                    self._finalize_task_cancellation(
                        session,
                        job=job,
                        task=task,
                        now=utc_now(),
                        reason=normalized_reason,
                        actor="queue",
                    )
                    return {
                        "task_id": task_id,
                        "queue_job_id": job.id,
                        "status": "cancelled",
                        "immediate": True,
                        "idempotent_replay": False,
                    }
                session.expire(job)
                session.refresh(job)

            if job.status == "leased":
                lease_result = session.execute(
                    update(QueueJobRecord)
                    .where(
                        QueueJobRecord.id == job.id,
                        QueueJobRecord.status == "leased",
                    )
                    .values(updated_at=now)
                    .execution_options(synchronize_session=False)
                )
                if lease_result.rowcount != 1:
                    session.expire(job)
                    session.refresh(job)
                    raise ValueError(
                        f"Task {task_id} changed while cancellation was requested"
                    )
                task_result = session.execute(
                    update(TaskRecord)
                    .where(
                        TaskRecord.id == task_id,
                        TaskRecord.status.in_(("pending", "running")),
                    )
                    .values(status="cancelling", updated_at=now)
                    .execution_options(synchronize_session=False)
                )
                if task_result.rowcount != 1:
                    session.expire(task)
                    session.refresh(task)
                    raise ValueError(
                        f"Task {task_id} cannot be cancelled from status {task.status}"
                    )
                self._add_task_event(
                    session,
                    task_id=task_id,
                    event_type="cancellation_requested",
                    actor=normalized_actor,
                    detail={
                        "reason": normalized_reason,
                        "queue_status": "leased",
                        "worker_id": job.lease_owner,
                    },
                    created_at=now,
                )
                return {
                    "task_id": task_id,
                    "queue_job_id": job.id,
                    "status": "cancelling",
                    "immediate": False,
                    "idempotent_replay": False,
                }

            if job.status == "cancelled":
                task.status = "cancelled"
                task.updated_at = now
                return {
                    "task_id": task_id,
                    "queue_job_id": job.id,
                    "status": "cancelled",
                    "immediate": True,
                    "idempotent_replay": True,
                }
            raise ValueError(
                f"Task {task_id} cannot be cancelled while queue job is {job.status}"
            )

    def is_task_cancellation_requested(self, task_id: str) -> bool:
        with self.database.session() as session:
            status = session.scalar(
                select(TaskRecord.status).where(TaskRecord.id == task_id)
            )
            if status is None:
                raise KeyError(f"TaskRecord not found: {task_id}")
            return status == "cancelling"

    def complete_task_cancellation(
        self,
        queue_job_id: str,
        *,
        worker_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.session() as session:
            job = self._required(session, QueueJobRecord, queue_job_id)
            if job.status == "cancelled":
                return _record_dict(job)
            if job.status != "leased" or job.lease_owner != worker_id:
                raise ValueError(
                    f"Queue job {queue_job_id} is not leased by {worker_id}"
                )
            task = self._required(session, TaskRecord, job.task_id)
            requested_reason = self._latest_cancellation_reason(session, task.id)
            if task.status != "cancelling" and requested_reason is None:
                raise ValueError(f"Task {task.id} has no cancellation request")

            normalized_reason = (reason or requested_reason or "").strip()
            normalized_reason = normalized_reason or "cancelled by user"
            self._finalize_task_cancellation(
                session,
                job=job,
                task=task,
                now=now,
                reason=normalized_reason,
                actor=worker_id,
            )
            session.flush()
            return _record_dict(job)

    def timeout_queue_job(
        self,
        queue_job_id: str,
        *,
        worker_id: str,
        timeout_seconds: float,
        retry_delay_seconds: float,
    ) -> dict[str, Any]:
        now = utc_now()
        timeout_seconds = max(0.0, timeout_seconds)
        reason = f"task execution exceeded {timeout_seconds:g} seconds"
        with self.database.session() as session:
            job = self._required(session, QueueJobRecord, queue_job_id)
            if job.status != "leased" or job.lease_owner != worker_id:
                raise ValueError(
                    f"Queue job {queue_job_id} is not leased by {worker_id}"
                )
            task = self._required(session, TaskRecord, job.task_id)
            if task.status == "cancelling":
                self._finalize_task_cancellation(
                    session,
                    job=job,
                    task=task,
                    now=now,
                    reason=(
                        self._latest_cancellation_reason(session, task.id)
                        or "cancelled by user"
                    ),
                    actor=worker_id,
                )
                session.flush()
                return _record_dict(job)
            self._mark_active_execution_stopped(
                session,
                task,
                now=now,
                reason=reason,
                status="timed_out",
            )

            retry_scheduled = job.attempts < job.max_attempts
            job.last_error = reason
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now
            if retry_scheduled:
                job.status = "queued"
                job.available_at = now + timedelta(
                    seconds=max(0.0, retry_delay_seconds)
                )
                task.status = "pending"
            else:
                job.status = "failed"
                job.finished_at = now
                task.status = "failed"
            task.updated_at = now
            self._add_task_event(
                session,
                task_id=task.id,
                event_type="execution_timed_out",
                actor=worker_id,
                detail={
                    "timeout_seconds": timeout_seconds,
                    "attempt": job.attempts,
                    "max_attempts": job.max_attempts,
                    "retry_scheduled": retry_scheduled,
                },
                created_at=now,
            )
            session.flush()
            return _record_dict(job)

    def claim_next_queue_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        lease_seconds = max(1, lease_seconds)
        for _ in range(8):
            now = utc_now()
            lease_expires_at = now + timedelta(seconds=lease_seconds)
            with self.database.session() as session:
                job = session.scalar(
                    select(QueueJobRecord)
                    .where(
                        or_(
                            and_(
                                QueueJobRecord.status == "queued",
                                QueueJobRecord.attempts
                                < QueueJobRecord.max_attempts,
                                QueueJobRecord.available_at <= now,
                            ),
                            and_(
                                QueueJobRecord.status == "leased",
                                QueueJobRecord.lease_expires_at <= now,
                            ),
                        ),
                    )
                    .order_by(
                        QueueJobRecord.available_at,
                        QueueJobRecord.created_at,
                    )
                    .limit(1)
                )
                if job is None:
                    return None

                task = self._required(session, TaskRecord, job.task_id)
                recovered = job.status == "leased"
                if recovered and task.status in {"cancelling", "cancelled"}:
                    cancellation_result = session.execute(
                        update(QueueJobRecord)
                        .where(
                            QueueJobRecord.id == job.id,
                            QueueJobRecord.status == "leased",
                            QueueJobRecord.lease_expires_at <= now,
                        )
                        .values(
                            status="cancelled",
                            lease_owner=None,
                            lease_expires_at=None,
                            finished_at=now,
                            updated_at=now,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if cancellation_result.rowcount != 1:
                        continue
                    session.expire(job)
                    session.refresh(job)
                    self._finalize_task_cancellation(
                        session,
                        job=job,
                        task=task,
                        now=now,
                        reason="cancelled after worker lease expired",
                        actor=worker_id,
                    )
                    continue
                if recovered and task.status in {
                    "awaiting_approval",
                    "completed",
                    "rejected",
                }:
                    job.status = "completed"
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.finished_at = now
                    job.updated_at = now
                    continue

                if recovered and job.attempts >= job.max_attempts:
                    self._mark_active_execution_abandoned(
                        session,
                        task,
                        now=now,
                        reason="worker lease expired during the final attempt",
                    )
                    task.status = "failed"
                    job.status = "failed"
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.last_error = "worker lease expired during the final attempt"
                    job.finished_at = now
                    job.updated_at = now
                    continue

                conditions = [
                    QueueJobRecord.id == job.id,
                    QueueJobRecord.status == job.status,
                    QueueJobRecord.attempts == job.attempts,
                ]
                if recovered:
                    conditions.append(QueueJobRecord.lease_expires_at <= now)
                else:
                    conditions.append(QueueJobRecord.available_at <= now)
                result = session.execute(
                    update(QueueJobRecord)
                    .where(*conditions)
                    .values(
                        status="leased",
                        attempts=job.attempts + 1,
                        lease_owner=worker_id,
                        lease_expires_at=lease_expires_at,
                        updated_at=now,
                        finished_at=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    continue
                session.refresh(job)
                if recovered:
                    self._mark_active_execution_abandoned(
                        session,
                        task,
                        now=now,
                        reason="worker lease expired before completion",
                    )
                payload = _record_dict(job)
                payload["recovered"] = recovered
                return payload
        return None

    def extend_queue_lease(
        self,
        queue_job_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        now = utc_now()
        with self.database.session() as session:
            result = session.execute(
                update(QueueJobRecord)
                .where(
                    QueueJobRecord.id == queue_job_id,
                    QueueJobRecord.status == "leased",
                    QueueJobRecord.lease_owner == worker_id,
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=max(1, lease_seconds)),
                    updated_at=now,
                )
            )
            return result.rowcount == 1

    def complete_queue_job(
        self,
        queue_job_id: str,
        *,
        worker_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.session() as session:
            job = self._required(session, QueueJobRecord, queue_job_id)
            if job.status == "completed":
                return _record_dict(job)
            if job.status != "leased" or job.lease_owner != worker_id:
                raise ValueError(
                    f"Queue job {queue_job_id} is not leased by {worker_id}"
                )
            task = self._required(session, TaskRecord, job.task_id)
            if task.status == "cancelling":
                self._finalize_task_cancellation(
                    session,
                    job=job,
                    task=task,
                    now=now,
                    reason=(
                        self._latest_cancellation_reason(session, task.id)
                        or "cancelled by user"
                    ),
                    actor=worker_id,
                )
                session.flush()
                return _record_dict(job)
            job.status = "completed"
            job.lease_owner = None
            job.lease_expires_at = None
            job.finished_at = now
            job.updated_at = now
            session.flush()
            return _record_dict(job)

    def fail_queue_job(
        self,
        queue_job_id: str,
        *,
        worker_id: str,
        error: str,
        retry_delay_seconds: float,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.session() as session:
            job = self._required(session, QueueJobRecord, queue_job_id)
            if job.status != "leased" or job.lease_owner != worker_id:
                raise ValueError(
                    f"Queue job {queue_job_id} is not leased by {worker_id}"
                )
            task = self._required(session, TaskRecord, job.task_id)
            if task.status == "cancelling":
                self._finalize_task_cancellation(
                    session,
                    job=job,
                    task=task,
                    now=now,
                    reason=(
                        self._latest_cancellation_reason(session, task.id)
                        or "cancelled by user"
                    ),
                    actor=worker_id,
                )
                session.flush()
                return _record_dict(job)
            job.last_error = error
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now
            if job.attempts < job.max_attempts:
                job.status = "queued"
                job.available_at = now + timedelta(
                    seconds=max(0.0, retry_delay_seconds)
                )
                task.status = "pending"
            else:
                job.status = "failed"
                job.finished_at = now
                task.status = "failed"
            session.flush()
            return _record_dict(job)

    def retry_failed_queue_job(self, task_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.database.session() as session:
            task = self._required(session, TaskRecord, task_id)
            job = session.scalar(
                select(QueueJobRecord).where(QueueJobRecord.task_id == task_id)
            )
            if job is None:
                raise ValueError(f"Task {task_id} was not created through the queue")
            if task.status != "failed" or job.status != "failed":
                raise ValueError(f"Task {task_id} is not in a retryable failed state")
            task.status = "pending"
            job.status = "queued"
            job.attempts = 0
            job.available_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.finished_at = None
            job.updated_at = now
            session.flush()
            return _record_dict(job)

    def mark_task_failed(self, task_id: str, *, error: str) -> None:
        with self.database.session() as session:
            task = self._required(session, TaskRecord, task_id)
            if task.status not in {
                "cancelling",
                "cancelled",
                "awaiting_approval",
                "completed",
                "rejected",
            }:
                task.status = "failed"
                task.updated_at = utc_now()

    def queue_summary(self) -> dict[str, int]:
        summary = {
            "queued": 0,
            "leased": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }
        with self.database.session() as session:
            rows = session.execute(
                select(QueueJobRecord.status, func.count(QueueJobRecord.id)).group_by(
                    QueueJobRecord.status
                )
            )
            for status, count in rows:
                summary[str(status)] = int(count)
        return summary

    @staticmethod
    def _ensure_user(session: Session, user_id: str) -> None:
        if session.get(UserRecord, user_id) is None:
            session.add(UserRecord(id=user_id, display_name=user_id))
            session.flush()

    @staticmethod
    def _queue_job_for_key(
        session: Session,
        *,
        user_id: str,
        idempotency_key: str | None,
    ) -> QueueJobRecord | None:
        if idempotency_key is None:
            return None
        return session.scalar(
            select(QueueJobRecord).where(
                QueueJobRecord.user_id == user_id,
                QueueJobRecord.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def _assert_idempotent_request_matches(
        task: TaskRecord,
        *,
        employee_id: str,
        workflow_name: str,
        task_input: dict[str, Any],
    ) -> None:
        if (
            task.employee_id != employee_id
            or task.workflow_name != workflow_name
            or task.input != task_input
        ):
            raise ValueError(
                "Idempotency key was already used with a different request"
            )

    @staticmethod
    def _mark_active_execution_abandoned(
        session: Session,
        task: TaskRecord,
        *,
        now: datetime,
        reason: str,
    ) -> None:
        active_runs = list(
            session.scalars(
                select(TaskRunRecord).where(
                    TaskRunRecord.task_id == task.id,
                    TaskRunRecord.status == "running",
                )
            )
        )
        run_ids = [run.id for run in active_runs]
        for run in active_runs:
            run.status = "failed"
            run.error = reason
            run.finished_at = now
            run.latency_ms = _elapsed_ms(run.started_at, now)
        if run_ids:
            workflow_runs = session.scalars(
                select(WorkflowRunRecord).where(
                    WorkflowRunRecord.task_run_id.in_(run_ids),
                    WorkflowRunRecord.status == "running",
                )
            )
            for workflow_run in workflow_runs:
                workflow_run.status = "failed"
                workflow_run.updated_at = now
        task.status = "pending"
        task.updated_at = now

    @staticmethod
    def _mark_active_execution_stopped(
        session: Session,
        task: TaskRecord,
        *,
        now: datetime,
        reason: str,
        status: str,
    ) -> None:
        active_runs = list(
            session.scalars(
                select(TaskRunRecord).where(
                    TaskRunRecord.task_id == task.id,
                    TaskRunRecord.status.in_(("running", "awaiting_approval")),
                )
            )
        )
        run_ids = [run.id for run in active_runs]
        for run in active_runs:
            run.status = status
            run.error = reason
            run.finished_at = now
            run.latency_ms = _elapsed_ms(run.started_at, now)
        if not run_ids:
            return
        workflow_runs = session.scalars(
            select(WorkflowRunRecord).where(
                WorkflowRunRecord.task_run_id.in_(run_ids),
                WorkflowRunRecord.status.in_(("running", "awaiting_approval")),
            )
        )
        for workflow_run in workflow_runs:
            workflow_run.status = status
            history = list(workflow_run.history or [])
            history.append(
                {
                    "step_id": workflow_run.current_step,
                    "status": status,
                    "finished_at": now.isoformat(),
                    "error": reason,
                }
            )
            workflow_run.history = history
            workflow_run.current_step = None
            workflow_run.updated_at = now

    @staticmethod
    def _add_task_event(
        session: Session,
        *,
        task_id: str,
        event_type: str,
        actor: str,
        detail: dict[str, Any],
        created_at: datetime,
    ) -> None:
        session.add(
            TaskEventRecord(
                id=_new_id(),
                task_id=task_id,
                event_type=event_type,
                actor=actor,
                detail=detail,
                created_at=created_at,
            )
        )

    @staticmethod
    def _latest_cancellation_reason(
        session: Session,
        task_id: str,
    ) -> str | None:
        event = session.scalar(
            select(TaskEventRecord)
            .where(
                TaskEventRecord.task_id == task_id,
                TaskEventRecord.event_type == "cancellation_requested",
            )
            .order_by(TaskEventRecord.created_at.desc())
            .limit(1)
        )
        if event is None:
            return None
        reason = str((event.detail or {}).get("reason", "")).strip()
        return reason or None

    @staticmethod
    def _finalize_task_cancellation(
        session: Session,
        *,
        job: QueueJobRecord,
        task: TaskRecord,
        now: datetime,
        reason: str,
        actor: str,
    ) -> None:
        execution_started = bool(
            session.scalar(
                select(func.count(TaskRunRecord.id)).where(
                    TaskRunRecord.task_id == task.id
                )
            )
        )
        ExecutionStore._mark_active_execution_stopped(
            session,
            task,
            now=now,
            reason=reason,
            status="cancelled",
        )
        pending_approvals = session.scalars(
            select(ApprovalRecord).where(
                ApprovalRecord.task_id == task.id,
                ApprovalRecord.status == "pending",
            )
        )
        for approval in pending_approvals:
            approval.status = "cancelled"
            approval.reason = reason
            approval.decided_at = now

        task.status = "cancelled"
        task.final_output = None
        task.updated_at = now
        job.status = "cancelled"
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error = reason
        job.finished_at = now
        job.updated_at = now
        ExecutionStore._add_task_event(
            session,
            task_id=task.id,
            event_type="cancellation_completed",
            actor=actor,
            detail={
                "reason": reason,
                "execution_started": execution_started,
                "attempt": job.attempts,
            },
            created_at=now,
        )

    def start_task_run(
        self,
        *,
        task_id: str,
        plan: list[dict[str, Any]],
    ) -> str:
        run_id = _new_id()
        with self.database.session() as session:
            task = self._required(session, TaskRecord, task_id)
            if task.status not in {"pending", "failed"}:
                raise ValueError(
                    f"Task {task_id} cannot start from status {task.status}"
                )
            task.status = "running"
            session.add(
                TaskRunRecord(
                    id=run_id,
                    task_id=task_id,
                    status="running",
                    plan=plan,
                )
            )
        return run_id

    def create_workflow_run(
        self,
        *,
        task_run_id: str,
        workflow_name: str,
        workflow_version: str,
        initial_state: dict[str, Any],
    ) -> str:
        workflow_run_id = _new_id()
        with self.database.session() as session:
            session.add(
                WorkflowRunRecord(
                    id=workflow_run_id,
                    task_run_id=task_run_id,
                    workflow_name=workflow_name,
                    workflow_version=workflow_version,
                    status="running",
                    state=initial_state,
                    history=[],
                )
            )
        return workflow_run_id

    def checkpoint_workflow(
        self,
        workflow_run_id: str,
        *,
        status: str,
        current_step: str | None,
        state: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> None:
        with self.database.session() as session:
            workflow_run = self._required(
                session, WorkflowRunRecord, workflow_run_id
            )
            workflow_run.status = status
            workflow_run.current_step = current_step
            workflow_run.state = state
            workflow_run.history = history
            workflow_run.updated_at = utc_now()

    def record_tool_call(
        self,
        *,
        task_run_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        status: str,
        output: dict[str, Any] | None,
        error: str | None,
        latency_ms: int,
    ) -> str:
        record_id = _new_id()
        with self.database.session() as session:
            session.add(
                ToolCallRecord(
                    id=record_id,
                    task_run_id=task_run_id,
                    tool_name=tool_name,
                    status=status,
                    input=tool_input,
                    output=output,
                    error=error,
                    latency_ms=latency_ms,
                )
            )
        return record_id

    def create_artifact(
        self,
        *,
        task_id: str,
        task_run_id: str,
        artifact_type: str,
        content: dict[str, Any],
        version: int = 1,
    ) -> str:
        artifact_id = _new_id()
        with self.database.session() as session:
            session.add(
                ArtifactRecord(
                    id=artifact_id,
                    task_id=task_id,
                    task_run_id=task_run_id,
                    artifact_type=artifact_type,
                    version=version,
                    content=content,
                )
            )
        return artifact_id

    def create_approval(
        self,
        *,
        task_id: str,
        task_run_id: str,
        action: str,
        proposed_output: dict[str, Any],
    ) -> str:
        approval_id = _new_id()
        with self.database.session() as session:
            session.add(
                ApprovalRecord(
                    id=approval_id,
                    task_id=task_id,
                    task_run_id=task_run_id,
                    action=action,
                    status="pending",
                    proposed_output=proposed_output,
                )
            )
        return approval_id

    def mark_execution_waiting(
        self,
        *,
        task_id: str,
        task_run_id: str,
        output: dict[str, Any],
    ) -> bool:
        with self.database.session() as session:
            task = self._required(session, TaskRecord, task_id)
            run = self._required(session, TaskRunRecord, task_run_id)
            if task.status == "cancelling":
                return False
            task.status = "awaiting_approval"
            run.status = "awaiting_approval"
            run.output = output
            return True

    def mark_execution_failed(
        self,
        *,
        task_id: str,
        task_run_id: str,
        workflow_run_id: str,
        error: str,
    ) -> bool:
        finished_at = utc_now()
        with self.database.session() as session:
            task = self._required(session, TaskRecord, task_id)
            run = self._required(session, TaskRunRecord, task_run_id)
            workflow_run = self._required(
                session, WorkflowRunRecord, workflow_run_id
            )
            if task.status == "cancelling":
                return False
            task.status = "failed"
            run.status = "failed"
            run.error = error
            run.finished_at = finished_at
            run.latency_ms = _elapsed_ms(run.started_at, finished_at)
            workflow_run.status = "failed"
            workflow_run.updated_at = finished_at
            return True

    def resolve_approval(
        self,
        approval_id: str,
        *,
        decision: str,
        edited_output: dict[str, Any] | None,
        reason: str | None,
    ) -> dict[str, Any]:
        if decision not in {"approved", "approved_with_edits", "rejected"}:
            raise ValueError(f"Unsupported approval decision: {decision}")
        if decision == "approved_with_edits" and edited_output is None:
            raise ValueError("edited_output is required for approved_with_edits")

        decided_at = utc_now()
        with self.database.session() as session:
            approval = self._required(session, ApprovalRecord, approval_id)
            if approval.status != "pending":
                raise ValueError(f"Approval {approval_id} has already been decided")
            task = self._required(session, TaskRecord, approval.task_id)
            run = self._required(session, TaskRunRecord, approval.task_run_id)
            workflow_run = session.scalar(
                select(WorkflowRunRecord).where(
                    WorkflowRunRecord.task_run_id == approval.task_run_id
                )
            )

            if decision == "approved":
                final_output = approval.proposed_output
                task_status = "completed"
            elif decision == "approved_with_edits":
                final_output = edited_output
                task_status = "completed"
            else:
                final_output = None
                task_status = "rejected"

            approval.status = decision
            approval.final_output = final_output
            approval.reason = reason
            approval.decided_at = decided_at
            task.status = task_status
            task.final_output = final_output
            run.status = "completed" if task_status == "completed" else "rejected"
            run.output = final_output or run.output
            run.finished_at = decided_at
            run.latency_ms = _elapsed_ms(run.started_at, decided_at)
            if workflow_run is not None:
                workflow_run.status = (
                    "completed" if task_status == "completed" else "rejected"
                )
                workflow_run.current_step = None
                workflow_run.updated_at = decided_at

            if decision == "approved_with_edits":
                session.add(
                    ArtifactRecord(
                        id=_new_id(),
                        task_id=task.id,
                        task_run_id=run.id,
                        artifact_type="project_maintenance_report",
                        version=2,
                        content=edited_output or {},
                    )
                )
                session.add(
                    FeedbackRecord(
                        id=_new_id(),
                        task_id=task.id,
                        approval_id=approval.id,
                        kind="user_edit",
                        original=approval.proposed_output,
                        revised=edited_output,
                        comment=reason,
                    )
                )
            elif decision == "rejected":
                session.add(
                    FeedbackRecord(
                        id=_new_id(),
                        task_id=task.id,
                        approval_id=approval.id,
                        kind="rejection",
                        original=approval.proposed_output,
                        revised=None,
                        comment=reason,
                    )
                )

            session.add(
                DecisionRecord(
                    id=_new_id(),
                    task_id=task.id,
                    approval_id=approval.id,
                    context={
                        "task_input": task.input,
                        "employee_id": task.employee_id,
                        "workflow_name": task.workflow_name,
                    },
                    options=["approved", "approved_with_edits", "rejected"],
                    agent_recommendation=approval.proposed_output,
                    user_choice=decision,
                    user_reason=reason,
                    final_outcome=final_output or {"status": "rejected"},
                )
            )
            session.flush()
            return _record_dict(approval)

    def add_feedback(
        self,
        task_id: str,
        *,
        comment: str,
        rating: int | None,
    ) -> str:
        feedback_id = _new_id()
        with self.database.session() as session:
            self._required(session, TaskRecord, task_id)
            session.add(
                FeedbackRecord(
                    id=feedback_id,
                    task_id=task_id,
                    approval_id=None,
                    kind="explicit_feedback",
                    original=None,
                    revised=None,
                    comment=comment,
                    rating=rating,
                )
            )
        return feedback_id

    def get_task_bundle(self, task_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            task = self._required(session, TaskRecord, task_id)
            runs = list(
                session.scalars(
                    select(TaskRunRecord)
                    .where(TaskRunRecord.task_id == task_id)
                    .order_by(TaskRunRecord.started_at)
                )
            )
            run_ids = [run.id for run in runs]
            tool_calls = (
                list(
                    session.scalars(
                        select(ToolCallRecord)
                        .where(ToolCallRecord.task_run_id.in_(run_ids))
                        .order_by(ToolCallRecord.created_at)
                    )
                )
                if run_ids
                else []
            )
            workflow_runs = (
                list(
                    session.scalars(
                        select(WorkflowRunRecord)
                        .where(WorkflowRunRecord.task_run_id.in_(run_ids))
                        .order_by(WorkflowRunRecord.created_at)
                    )
                )
                if run_ids
                else []
            )
            approvals = list(
                session.scalars(
                    select(ApprovalRecord)
                    .where(ApprovalRecord.task_id == task_id)
                    .order_by(ApprovalRecord.created_at)
                )
            )
            feedback = list(
                session.scalars(
                    select(FeedbackRecord)
                    .where(FeedbackRecord.task_id == task_id)
                    .order_by(FeedbackRecord.created_at)
                )
            )
            artifacts = list(
                session.scalars(
                    select(ArtifactRecord)
                    .where(ArtifactRecord.task_id == task_id)
                    .order_by(ArtifactRecord.version)
                )
            )
            decisions = list(
                session.scalars(
                    select(DecisionRecord)
                    .where(DecisionRecord.task_id == task_id)
                    .order_by(DecisionRecord.created_at)
                )
            )
            queue_jobs = list(
                session.scalars(
                    select(QueueJobRecord)
                    .where(QueueJobRecord.task_id == task_id)
                    .order_by(QueueJobRecord.created_at)
                )
            )
            task_events = list(
                session.scalars(
                    select(TaskEventRecord)
                    .where(TaskEventRecord.task_id == task_id)
                    .order_by(TaskEventRecord.created_at, TaskEventRecord.id)
                )
            )
            return {
                "task": _record_dict(task),
                "runs": [_record_dict(item) for item in runs],
                "tool_calls": [_record_dict(item) for item in tool_calls],
                "workflow_runs": [_record_dict(item) for item in workflow_runs],
                "approvals": [_record_dict(item) for item in approvals],
                "feedback": [_record_dict(item) for item in feedback],
                "artifacts": [_record_dict(item) for item in artifacts],
                "decision_records": [_record_dict(item) for item in decisions],
                "queue_jobs": [_record_dict(item) for item in queue_jobs],
                "task_events": [_record_dict(item) for item in task_events],
            }

    def list_tasks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.session() as session:
            tasks = session.scalars(
                select(TaskRecord)
                .order_by(TaskRecord.created_at.desc())
                .limit(max(1, min(limit, 200)))
            )
            return [_record_dict(task) for task in tasks]

    def list_employees(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            records = session.scalars(
                select(EmployeeRecord).order_by(EmployeeRecord.id)
            )
            return [_record_dict(item) for item in records]

    def list_skills(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            records = session.scalars(select(SkillRecord).order_by(SkillRecord.id))
            return [_record_dict(item) for item in records]

    @staticmethod
    def _required(session: Session, model: Any, record_id: str) -> Any:
        record = session.get(model, record_id)
        if record is None:
            raise KeyError(f"{model.__name__} not found: {record_id}")
        return record
