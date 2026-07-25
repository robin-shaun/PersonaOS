from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from core.bootstrap import Container
from core.security.access import AccessContext
from core.security.auth_key import LocalAuthKey
from core.security.authentication import AuthenticationService
from core.services.legacy_owner_migration import LegacyOwnerMigrationService
from core.storage.auth_repository import AuthRepository
from core.storage.models import (
    AuditEventRecord,
    LegacyOwnerMigrationRecord,
    PersonaRecord,
    TaskRecord,
)

PASSWORD = "migration-test-password-123"


def _authentication(
    container: Container,
    tmp_path,
) -> AuthenticationService:
    return AuthenticationService(
        repository=AuthRepository(container.database),
        auth_key=LocalAuthKey(tmp_path / "migration-auth.key"),
        idle_seconds=1800,
        absolute_seconds=43_200,
        reauthentication_seconds=300,
        max_sessions=5,
        failure_limit=5,
        lockout_seconds=60,
        password_hasher=PasswordHasher(
            time_cost=1,
            memory_cost=8 * 1024,
            parallelism=1,
            hash_len=32,
            salt_len=16,
        ),
    )


def _create_target_account(
    container: Container,
    tmp_path,
    *,
    username: str = "migration-admin",
) -> dict[str, object]:
    return _authentication(container, tmp_path).create_account(
        username=username,
        display_name="Migration Admin",
        password=PASSWORD,
        role="admin",
    )


def test_legacy_owner_migration_previews_applies_and_exactly_rolls_back(
    container: Container,
    tmp_path,
) -> None:
    source = AccessContext(
        owner_id="local-user",
        actor_id="local-user",
    )
    persona = container.personas.create(
        source,
        display_name="Legacy Persona",
    )
    target = _create_target_account(container, tmp_path)
    target_access = AccessContext(
        owner_id=str(target["id"]),
        actor_id=str(target["id"]),
    )
    migration = LegacyOwnerMigrationService(container.database)

    preview = migration.preview(
        source_owner_id="local-user",
        target_username="migration-admin",
    )
    assert preview["can_apply"] is True
    assert preview["counts"]["personas.owner_id"] == 1
    assert preview["applied"] is False

    applied = migration.apply(
        source_owner_id="local-user",
        target_username="migration-admin",
    )
    assert applied["applied"] is True
    assert applied["total_rows"] == preview["total_rows"]
    assert container.personas.get(target_access, persona["id"])["owner_id"] == (
        target["id"]
    )
    with pytest.raises(KeyError):
        container.personas.get(source, persona["id"])

    rolled_back = migration.rollback(applied["receipt_id"])
    assert rolled_back["rolled_back"] is True
    assert container.personas.get(source, persona["id"])["owner_id"] == (
        "local-user"
    )
    with pytest.raises(KeyError):
        container.personas.get(target_access, persona["id"])

    with container.database.session(system=True) as session:
        receipt = session.get(
            LegacyOwnerMigrationRecord,
            applied["receipt_id"],
        )
        assert receipt is not None
        assert receipt.status == "rolled_back"
        assert receipt.rolled_back_at is not None


def test_legacy_owner_migration_rejects_nonterminal_tasks_and_conflicts(
    container: Container,
    tmp_path,
) -> None:
    target = _create_target_account(container, tmp_path)
    migration = LegacyOwnerMigrationService(container.database)
    task_id = container.store.create_task(
        employee_id="github-maintainer-001",
        user_id="local-user",
        workflow_name="daily-project-maintenance",
        task_input={"repository": "example/project"},
    )

    preview = migration.preview(
        source_owner_id="local-user",
        target_username="migration-admin",
    )
    assert preview["can_apply"] is False
    assert preview["nonterminal_task_ids"] == [task_id]
    with pytest.raises(ValueError, match="all tasks to be terminal"):
        migration.apply(
            source_owner_id="local-user",
            target_username="migration-admin",
        )

    with container.database.session(system=True) as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        task.status = "completed"

    verified_at = datetime(2026, 7, 25, tzinfo=UTC)
    for owner_id in ("local-user", str(target["id"])):
        container.store.upsert_github_connection(
            user_id=owner_id,
            installation_id=123,
            repository="example/private",
            account_login="example",
            private=True,
            permissions={"contents": "read"},
            repository_selection="selected",
            verified_at=verified_at,
        )

    conflicted = migration.preview(
        source_owner_id="local-user",
        target_username="migration-admin",
    )
    assert conflicted["can_apply"] is False
    assert any(
        item.startswith("employee_assignments:")
        or item.startswith("github_connections:")
        for item in conflicted["conflicts"]
    )
    with pytest.raises(ValueError, match="uniqueness conflicts"):
        migration.apply(
            source_owner_id="local-user",
            target_username="migration-admin",
        )


def test_legacy_owner_rollback_is_atomic_when_manifest_diverges(
    container: Container,
    tmp_path,
) -> None:
    source = AccessContext(
        owner_id="local-user",
        actor_id="local-user",
    )
    persona = container.personas.create(
        source,
        display_name="Divergence Persona",
    )
    target = _create_target_account(container, tmp_path)
    second_target = _authentication(container, tmp_path).create_account(
        username="second-target",
        display_name="Second Target",
        password=PASSWORD,
        role="member",
    )
    migration = LegacyOwnerMigrationService(container.database)
    applied = migration.apply(
        source_owner_id="local-user",
        target_username="migration-admin",
    )

    with container.database.session(system=True) as session:
        record = session.get(PersonaRecord, persona["id"])
        assert record is not None
        record.owner_id = str(second_target["id"])

    with pytest.raises(ValueError, match="rollback divergence"):
        migration.rollback(applied["receipt_id"])

    with container.database.session(system=True) as session:
        record = session.get(PersonaRecord, persona["id"])
        receipt = session.get(
            LegacyOwnerMigrationRecord,
            applied["receipt_id"],
        )
        assert record is not None
        assert record.owner_id == second_target["id"]
        assert receipt is not None
        assert receipt.status == "applied"
        assert receipt.target_user_id == target["id"]


def test_legacy_owner_preview_detects_audit_dedupe_conflict(
    container: Container,
    tmp_path,
) -> None:
    source = AccessContext(
        owner_id="local-user",
        actor_id="local-user",
    )
    container.personas.create(source, display_name="Audited Legacy Persona")
    target = _create_target_account(container, tmp_path)

    with container.database.session(system=True) as session:
        source_event = session.scalar(
            select(AuditEventRecord)
            .where(AuditEventRecord.owner_id == "local-user")
            .order_by(AuditEventRecord.occurred_at, AuditEventRecord.id)
        )
        assert source_event is not None
        session.add(
            AuditEventRecord(
                id=str(uuid4()),
                dedupe_key=source_event.dedupe_key,
                actor_type="local_account",
                actor_id=str(target["id"]),
                owner_id=str(target["id"]),
                persona_id=None,
                action="test.audit_conflict",
                resource_type="test",
                resource_id="audit-conflict",
                outcome="succeeded",
                risk_level="low",
                detail={},
            )
        )

    preview = LegacyOwnerMigrationService(container.database).preview(
        source_owner_id="local-user",
        target_username="migration-admin",
    )

    assert preview["can_apply"] is False
    assert f"audit_events:{source_event.id}" in preview["conflicts"]
