from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
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
    SkillRecord,
    SkillVersionRecord,
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
            for definition in employees:
                record = session.get(EmployeeRecord, definition.employee_id)
                payload = definition.model_dump(mode="json")
                if record is None:
                    session.add(
                        EmployeeRecord(
                            id=definition.employee_id,
                            name=definition.name,
                            role=definition.role,
                            definition=payload,
                        )
                    )
                else:
                    record.name = definition.name
                    record.role = definition.role
                    record.definition = payload

            for definition in skills:
                skill = session.get(SkillRecord, definition.name)
                if skill is None:
                    session.add(
                        SkillRecord(
                            id=definition.name,
                            description=definition.description,
                        )
                    )
                    session.flush()
                else:
                    skill.description = definition.description
                version = session.scalar(
                    select(SkillVersionRecord).where(
                        SkillVersionRecord.skill_id == definition.name,
                        SkillVersionRecord.version == definition.version,
                    )
                )
                payload = definition.model_dump(mode="json")
                if version is None:
                    session.add(
                        SkillVersionRecord(
                            id=_new_id(),
                            skill_id=definition.name,
                            version=definition.version,
                            definition=payload,
                        )
                    )
                else:
                    version.definition = payload

            for definition in workflows:
                record = session.scalar(
                    select(WorkflowRecord).where(
                        WorkflowRecord.name == definition.name,
                        WorkflowRecord.version == definition.version,
                    )
                )
                payload = definition.model_dump(mode="json")
                if record is None:
                    session.add(
                        WorkflowRecord(
                            id=_new_id(),
                            name=definition.name,
                            version=definition.version,
                            definition=payload,
                        )
                    )
                else:
                    record.definition = payload

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

    @staticmethod
    def _ensure_user(session: Session, user_id: str) -> None:
        if session.get(UserRecord, user_id) is None:
            session.add(UserRecord(id=user_id, display_name=user_id))
            session.flush()

    def start_task_run(
        self,
        *,
        task_id: str,
        plan: list[dict[str, Any]],
    ) -> str:
        run_id = _new_id()
        with self.database.session() as session:
            task = self._required(session, TaskRecord, task_id)
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
    ) -> None:
        with self.database.session() as session:
            task = self._required(session, TaskRecord, task_id)
            run = self._required(session, TaskRunRecord, task_run_id)
            task.status = "awaiting_approval"
            run.status = "awaiting_approval"
            run.output = output

    def mark_execution_failed(
        self,
        *,
        task_id: str,
        task_run_id: str,
        workflow_run_id: str,
        error: str,
    ) -> None:
        finished_at = utc_now()
        with self.database.session() as session:
            task = self._required(session, TaskRecord, task_id)
            run = self._required(session, TaskRunRecord, task_run_id)
            workflow_run = self._required(
                session, WorkflowRunRecord, workflow_run_id
            )
            task.status = "failed"
            run.status = "failed"
            run.error = error
            run.finished_at = finished_at
            run.latency_ms = _elapsed_ms(run.started_at, finished_at)
            workflow_run.status = "failed"
            workflow_run.updated_at = finished_at

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
            return {
                "task": _record_dict(task),
                "runs": [_record_dict(item) for item in runs],
                "tool_calls": [_record_dict(item) for item in tool_calls],
                "workflow_runs": [_record_dict(item) for item in workflow_runs],
                "approvals": [_record_dict(item) for item in approvals],
                "feedback": [_record_dict(item) for item in feedback],
                "artifacts": [_record_dict(item) for item in artifacts],
                "decision_records": [_record_dict(item) for item in decisions],
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

