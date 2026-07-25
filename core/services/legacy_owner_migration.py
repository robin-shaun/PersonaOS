from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update

from core.storage.database import Database
from core.storage.models import (
    AuditEventRecord,
    AuthEventRecord,
    ConversationMessageRecord,
    ConversationRecord,
    EmployeeAssignmentRecord,
    GitHubConnectionRecord,
    LegacyOwnerMigrationRecord,
    MemorySourceRecord,
    PersonaMemoryEmbeddingRecord,
    PersonaMemoryRecord,
    PersonaMemoryRelationRecord,
    PersonaRecord,
    PreferenceRecord,
    PreferenceReviewRecord,
    QueueJobRecord,
    RetrievalRunRecord,
    SourceDocumentRecord,
    TaskRecord,
    UserRecord,
    utc_now,
)


def _new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True, slots=True)
class _OwnerField:
    model: Any
    column_name: str

    @property
    def key(self) -> str:
        return f"{self.model.__tablename__}.{self.column_name}"


_OWNER_FIELDS: tuple[_OwnerField, ...] = (
    _OwnerField(EmployeeAssignmentRecord, "user_id"),
    _OwnerField(GitHubConnectionRecord, "user_id"),
    _OwnerField(PersonaRecord, "owner_id"),
    _OwnerField(PreferenceRecord, "user_id"),
    _OwnerField(TaskRecord, "user_id"),
    _OwnerField(MemorySourceRecord, "user_id"),
    _OwnerField(ConversationRecord, "owner_id"),
    _OwnerField(PreferenceReviewRecord, "user_id"),
    _OwnerField(QueueJobRecord, "user_id"),
    _OwnerField(SourceDocumentRecord, "owner_id"),
    _OwnerField(ConversationMessageRecord, "owner_id"),
    _OwnerField(PersonaMemoryRecord, "owner_id"),
    _OwnerField(AuditEventRecord, "owner_id"),
    _OwnerField(PersonaMemoryRelationRecord, "owner_id"),
    _OwnerField(RetrievalRunRecord, "owner_id"),
    _OwnerField(PersonaMemoryEmbeddingRecord, "owner_id"),
)

_UNIQUE_SCOPES: tuple[tuple[Any, tuple[str, ...]], ...] = (
    (EmployeeAssignmentRecord, ("employee_id",)),
    (GitHubConnectionRecord, ("provider", "repository")),
    (PreferenceRecord, ("fingerprint",)),
    (QueueJobRecord, ("idempotency_key",)),
    (MemorySourceRecord, ("source_type", "source_id")),
    (AuditEventRecord, ("dedupe_key",)),
)

_NONTERMINAL_TASK_STATUSES = frozenset(
    {"pending", "running", "cancelling", "awaiting_approval"}
)


class LegacyOwnerMigrationService:
    """Explicitly transfer 0.11 owner rows and retain an exact rollback manifest."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def preview(
        self,
        *,
        source_owner_id: str,
        target_username: str,
    ) -> dict[str, Any]:
        source_owner_id = self._normalize_owner(source_owner_id)
        target_username = target_username.strip().lower()
        with self.database.session(system=True) as session:
            source, target = self._resolve_accounts(
                session,
                source_owner_id=source_owner_id,
                target_username=target_username,
            )
            manifest = self._manifest(
                session,
                owner_id=source_owner_id,
            )
            conflicts = self._conflicts(
                session,
                source_owner_id=source_owner_id,
                target_user_id=target.id,
            )
            active_task_ids = list(
                session.scalars(
                    select(TaskRecord.id).where(
                        TaskRecord.user_id == source_owner_id,
                        TaskRecord.status.in_(_NONTERMINAL_TASK_STATUSES),
                    )
                )
            )
            return {
                "source_owner": {
                    "id": source.id,
                    "display_name": source.display_name,
                    "status": source.status,
                },
                "target_account": {
                    "id": target.id,
                    "username": target.username,
                    "display_name": target.display_name,
                    "role": target.role,
                },
                "counts": {
                    key: len(record_ids)
                    for key, record_ids in manifest.items()
                },
                "total_rows": sum(len(value) for value in manifest.values()),
                "conflicts": conflicts,
                "nonterminal_task_ids": active_task_ids,
                "can_apply": not conflicts and not active_task_ids,
                "applied": False,
            }

    def apply(
        self,
        *,
        source_owner_id: str,
        target_username: str,
    ) -> dict[str, Any]:
        source_owner_id = self._normalize_owner(source_owner_id)
        target_username = target_username.strip().lower()
        now = utc_now()
        with self.database.session(system=True) as session:
            source, target = self._resolve_accounts(
                session,
                source_owner_id=source_owner_id,
                target_username=target_username,
                lock=True,
            )
            manifest = self._manifest(session, owner_id=source_owner_id)
            conflicts = self._conflicts(
                session,
                source_owner_id=source_owner_id,
                target_user_id=target.id,
            )
            if conflicts:
                raise ValueError(
                    "legacy owner migration has uniqueness conflicts: "
                    + ", ".join(conflicts)
                )
            active_task_ids = list(
                session.scalars(
                    select(TaskRecord.id).where(
                        TaskRecord.user_id == source_owner_id,
                        TaskRecord.status.in_(_NONTERMINAL_TASK_STATUSES),
                    )
                )
            )
            if active_task_ids:
                raise ValueError(
                    "legacy owner migration requires all tasks to be terminal"
                )
            receipt_id = _new_id()
            for field in _OWNER_FIELDS:
                record_ids = manifest[field.key]
                if not record_ids:
                    continue
                result = session.execute(
                    update(field.model)
                    .where(
                        field.model.id.in_(record_ids),
                        getattr(field.model, field.column_name)
                        == source_owner_id,
                    )
                    .values({field.column_name: target.id})
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != len(record_ids):
                    raise ValueError(
                        f"legacy owner migration changed while applying {field.key}"
                    )
            session.add(
                LegacyOwnerMigrationRecord(
                    id=receipt_id,
                    source_owner_id=source.id,
                    target_user_id=target.id,
                    status="applied",
                    manifest=manifest,
                    created_by_id=target.id,
                    created_at=now,
                )
            )
            session.add(
                AuthEventRecord(
                    id=_new_id(),
                    account_id=target.id,
                    actor_id=target.id,
                    action="legacy_owner.migrated",
                    outcome="succeeded",
                    detail={
                        "receipt_id": receipt_id,
                        "source_owner_id": source.id,
                        "total_rows": sum(
                            len(value) for value in manifest.values()
                        ),
                    },
                )
            )
            return {
                "receipt_id": receipt_id,
                "source_owner_id": source.id,
                "target_account_id": target.id,
                "counts": {
                    key: len(record_ids)
                    for key, record_ids in manifest.items()
                },
                "total_rows": sum(len(value) for value in manifest.values()),
                "applied": True,
            }

    def rollback(self, receipt_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.database.session(system=True) as session:
            receipt = session.get(
                LegacyOwnerMigrationRecord,
                receipt_id,
                with_for_update=True,
            )
            if receipt is None:
                raise KeyError(
                    f"LegacyOwnerMigrationRecord not found: {receipt_id}"
                )
            if receipt.status != "applied":
                raise ValueError(
                    f"legacy owner migration {receipt_id} is already rolled back"
                )
            source = session.get(UserRecord, receipt.source_owner_id)
            target = session.get(UserRecord, receipt.target_user_id)
            if source is None or target is None:
                raise ValueError("migration account no longer exists")
            manifest = {
                str(key): [str(item) for item in value]
                for key, value in (receipt.manifest or {}).items()
            }
            expected_keys = {field.key for field in _OWNER_FIELDS}
            if set(manifest) != expected_keys:
                raise ValueError("migration receipt manifest is invalid")
            for field in _OWNER_FIELDS:
                record_ids = manifest[field.key]
                if not record_ids:
                    continue
                current_ids = set(
                    session.scalars(
                        select(field.model.id).where(
                            field.model.id.in_(record_ids),
                            getattr(field.model, field.column_name)
                            == target.id,
                        )
                    )
                )
                if current_ids != set(record_ids):
                    raise ValueError(
                        f"migration rollback divergence in {field.key}"
                    )
            conflicts = self._conflicts(
                session,
                source_owner_id=target.id,
                target_user_id=source.id,
                restrict_ids=manifest,
            )
            if conflicts:
                raise ValueError(
                    "migration rollback has uniqueness conflicts: "
                    + ", ".join(conflicts)
                )
            for field in reversed(_OWNER_FIELDS):
                record_ids = manifest[field.key]
                if not record_ids:
                    continue
                result = session.execute(
                    update(field.model)
                    .where(
                        field.model.id.in_(record_ids),
                        getattr(field.model, field.column_name) == target.id,
                    )
                    .values({field.column_name: source.id})
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != len(record_ids):
                    raise ValueError(
                        f"migration rollback changed while applying {field.key}"
                    )
            receipt.status = "rolled_back"
            receipt.rolled_back_at = now
            session.add(
                AuthEventRecord(
                    id=_new_id(),
                    account_id=target.id,
                    actor_id=target.id,
                    action="legacy_owner.migration_rolled_back",
                    outcome="succeeded",
                    detail={
                        "receipt_id": receipt.id,
                        "source_owner_id": source.id,
                        "total_rows": sum(
                            len(value) for value in manifest.values()
                        ),
                    },
                )
            )
            return {
                "receipt_id": receipt.id,
                "source_owner_id": source.id,
                "target_account_id": target.id,
                "total_rows": sum(len(value) for value in manifest.values()),
                "rolled_back": True,
            }

    @staticmethod
    def _normalize_owner(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("legacy owner ID is invalid")
        return normalized

    @staticmethod
    def _resolve_accounts(
        session,
        *,
        source_owner_id: str,
        target_username: str,
        lock: bool = False,
    ) -> tuple[UserRecord, UserRecord]:
        source_statement = select(UserRecord).where(
            UserRecord.id == source_owner_id
        )
        target_statement = select(UserRecord).where(
            UserRecord.username == target_username
        )
        if lock:
            source_statement = source_statement.with_for_update()
            target_statement = target_statement.with_for_update()
        source = session.scalar(source_statement)
        target = session.scalar(target_statement)
        if source is None:
            raise KeyError(f"legacy owner not found: {source_owner_id}")
        if target is None or target.status != "active":
            raise KeyError(f"active target account not found: {target_username}")
        if source.id == target.id:
            raise ValueError("source owner and target account must differ")
        return source, target

    @staticmethod
    def _manifest(session, *, owner_id: str) -> dict[str, list[str]]:
        output: dict[str, list[str]] = {}
        for field in _OWNER_FIELDS:
            output[field.key] = list(
                session.scalars(
                    select(field.model.id)
                    .where(
                        getattr(field.model, field.column_name) == owner_id
                    )
                    .order_by(field.model.id)
                )
            )
        return output

    @staticmethod
    def _conflicts(
        session,
        *,
        source_owner_id: str,
        target_user_id: str,
        restrict_ids: dict[str, list[str]] | None = None,
    ) -> list[str]:
        conflicts: list[str] = []
        field_by_model = {field.model: field for field in _OWNER_FIELDS}
        for model, columns in _UNIQUE_SCOPES:
            field = field_by_model[model]
            source_statement = select(model).where(
                getattr(model, field.column_name) == source_owner_id
            )
            if restrict_ids is not None:
                source_statement = source_statement.where(
                    model.id.in_(restrict_ids[field.key])
                )
            source_rows: Sequence[Any] = list(
                session.scalars(source_statement)
            )
            target_rows: Sequence[Any] = list(
                session.scalars(
                    select(model).where(
                        getattr(model, field.column_name) == target_user_id
                    )
                )
            )
            target_values = {
                tuple(getattr(item, column) for column in columns)
                for item in target_rows
                if all(
                    getattr(item, column) is not None for column in columns
                )
            }
            for item in source_rows:
                values = tuple(getattr(item, column) for column in columns)
                if any(value is None for value in values):
                    continue
                if values in target_values:
                    conflicts.append(f"{model.__tablename__}:{item.id}")
        return sorted(conflicts)
