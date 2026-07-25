from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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


class MemorySourceRecord(Base):
    __tablename__ = "memory_sources"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source_type",
            "source_id",
            name="uq_memory_source_provenance",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id"), index=True, nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(80), index=True)
    source_id: Mapped[str] = mapped_column(String(100), index=True)
    source_kind: Mapped[str] = mapped_column(String(80), index=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PreferenceRecord(Base):
    __tablename__ = "preferences"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "fingerprint",
            name="uq_preference_user_fingerprint",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    context: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    rule: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(40), default="candidate", index=True, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_evidenced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class PreferenceEvidenceRecord(Base):
    __tablename__ = "preference_evidence"
    __table_args__ = (
        UniqueConstraint(
            "preference_id",
            "memory_source_id",
            name="uq_preference_memory_source",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    preference_id: Mapped[str] = mapped_column(
        ForeignKey("preferences.id"), index=True
    )
    memory_source_id: Mapped[str] = mapped_column(
        ForeignKey("memory_sources.id"), index=True
    )
    extraction_method: Mapped[str] = mapped_column(String(100))
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PreferenceReviewRecord(Base):
    __tablename__ = "preference_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    preference_id: Mapped[str] = mapped_column(
        ForeignKey("preferences.id"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    previous_status: Mapped[str] = mapped_column(String(40))
    new_status: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PersonaRecord(Base):
    __tablename__ = "personas"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'deleted')",
            name="ck_persona_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    simulation_notice: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default="active", index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SourceDocumentRecord(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint(
            "persona_id",
            "content_sha256",
            name="uq_source_document_persona_content",
        ),
        CheckConstraint(
            "status IN ('uploaded', 'processing', 'ready', 'failed', "
            "'deleting', 'deleted')",
            name="ck_source_document_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id"), index=True, nullable=True
    )
    source_type: Mapped[str] = mapped_column(
        String(80), default="uploaded_text", index=True, nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(120))
    language: Mapped[str | None] = mapped_column(String(40), nullable=True)
    object_key: Mapped[str] = mapped_column(String(200))
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(
        String(40), default="uploaded", index=True, nullable=False
    )
    ingestion_version: Mapped[str] = mapped_column(
        String(80), default="text-v1", nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DocumentChunkRecord(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "ordinal",
            name="uq_document_chunk_ordinal",
        ),
        UniqueConstraint(
            "document_id",
            "char_start",
            "char_end",
            name="uq_document_chunk_location",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id"), index=True
    )
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    chunker_name: Mapped[str] = mapped_column(String(100))
    chunker_version: Mapped[str] = mapped_column(String(50))
    chunker_config_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PersonaMemoryRecord(Base):
    __tablename__ = "persona_memories"
    __table_args__ = (
        UniqueConstraint(
            "persona_id",
            "candidate_fingerprint",
            name="uq_persona_memory_candidate",
        ),
        CheckConstraint(
            "status IN ('candidate', 'confirmed', 'rejected', "
            "'superseded', 'deleted')",
            name="ck_persona_memory_status",
        ),
        CheckConstraint(
            "memory_type IN ('episodic', 'semantic', 'procedural', "
            "'preference', 'relationship', 'reflection')",
            name="ck_persona_memory_type",
        ),
        CheckConstraint(
            "epistemic_status IN ('user_asserted', 'source_verified', "
            "'model_summary', 'model_inference', 'user_rule')",
            name="ck_persona_memory_epistemic_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id"), index=True
    )
    memory_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(
        String(40), default="candidate", index=True, nullable=False
    )
    epistemic_status: Mapped[str] = mapped_column(String(40), index=True)
    current_version_id: Mapped[str | None] = mapped_column(
        String(36), index=True, nullable=True
    )
    candidate_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    sensitivity: Mapped[str] = mapped_column(
        String(40), default="private", index=True, nullable=False
    )
    visibility: Mapped[str] = mapped_column(
        String(40), default="owner", index=True, nullable=False
    )
    event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PersonaMemoryVersionRecord(Base):
    __tablename__ = "persona_memory_versions"
    __table_args__ = (
        UniqueConstraint(
            "memory_id",
            "version",
            name="uq_persona_memory_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("persona_memories.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    raw_content: Mapped[str] = mapped_column(Text)
    structured_summary: Mapped[str] = mapped_column(Text)
    metadata_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_by_type: Mapped[str] = mapped_column(String(40))
    created_by_id: Mapped[str] = mapped_column(String(200))
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    extractor_name: Mapped[str] = mapped_column(String(100))
    extractor_version: Mapped[str] = mapped_column(String(50))
    model_call_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PersonaMemoryEvidenceRecord(Base):
    __tablename__ = "persona_memory_evidence"
    __table_args__ = (
        UniqueConstraint(
            "memory_version_id",
            "document_chunk_id",
            "relation",
            name="uq_persona_memory_evidence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_version_id: Mapped[str] = mapped_column(
        ForeignKey("persona_memory_versions.id"), index=True
    )
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id"), index=True
    )
    document_chunk_id: Mapped[str] = mapped_column(
        ForeignKey("document_chunks.id"), index=True
    )
    relation: Mapped[str] = mapped_column(
        String(40), default="supports", nullable=False
    )
    locator_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    excerpt: Mapped[str] = mapped_column(Text)
    excerpt_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AuditEventRecord(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "dedupe_key",
            name="uq_audit_event_owner_dedupe",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True, nullable=False
    )
    request_id: Mapped[str | None] = mapped_column(
        String(100), index=True, nullable=True
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(100), index=True, nullable=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(300))
    actor_type: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    persona_id: Mapped[str | None] = mapped_column(
        ForeignKey("personas.id"), index=True, nullable=True
    )
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str] = mapped_column(String(80), index=True)
    resource_id: Mapped[str] = mapped_column(String(100), index=True)
    outcome: Mapped[str] = mapped_column(
        String(40), default="succeeded", index=True, nullable=False
    )
    risk_level: Mapped[str] = mapped_column(
        String(40), default="low", nullable=False
    )
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("approvals.id"), index=True, nullable=True
    )
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
