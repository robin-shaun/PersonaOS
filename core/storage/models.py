from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class GitHubConnectionRecord(Base):
    __tablename__ = "github_connections"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            "repository",
            name="uq_github_connection_user_repository",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="github_app")
    installation_id: Mapped[int] = mapped_column(BigInteger, index=True)
    repository: Mapped[str] = mapped_column(String(300), index=True)
    account_login: Mapped[str] = mapped_column(String(200), index=True)
    private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    permissions: Mapped[dict[str, str]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    repository_selection: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EmployeeRecord(Base):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(100), index=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class EmployeeAssignmentRecord(Base):
    __tablename__ = "employee_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "employee_id", name="uq_employee_assignment"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class SkillRecord(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class SkillVersionRecord(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), index=True)
    version: Mapped[str] = mapped_column(String(50))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class WorkflowRecord(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_workflow_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(50))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workflow_name: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    final_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class QueueJobRecord(Base):
    __tablename__ = "queue_jobs"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_queue_job_task"),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_queue_job_user_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True, nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskEventRecord(Base):
    __tablename__ = "task_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class TaskRunRecord(Base):
    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    plan: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ToolCallRecord(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(40))
    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class WorkflowRunRecord(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id"), index=True)
    workflow_name: Mapped[str] = mapped_column(String(120), index=True)
    workflow_version: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(40), index=True)
    current_step: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ApprovalRecord(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    task_run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id"), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    proposed_output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    final_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FeedbackRecord(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("approvals.id"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(60), index=True)
    original: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    revised: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("task_id", "artifact_type", "version", name="uq_artifact_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    task_run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class DecisionRecord(Base):
    __tablename__ = "decision_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"), index=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    options: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    agent_recommendation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    user_choice: Mapped[str] = mapped_column(String(60))
    user_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
