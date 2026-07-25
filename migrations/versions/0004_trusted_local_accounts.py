"""trusted local accounts and owner row security

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-25 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIRECT_OWNER_COLUMNS = {
    "users": "id",
    "auth_sessions": "user_id",
    "auth_events": "account_id",
    "legacy_owner_migrations": "target_user_id",
    "employee_assignments": "user_id",
    "github_connections": "user_id",
    "personas": "owner_id",
    "preferences": "user_id",
    "tasks": "user_id",
    "memory_sources": "user_id",
    "persona_conversations": "owner_id",
    "preference_reviews": "user_id",
    "queue_jobs": "user_id",
    "source_documents": "owner_id",
    "persona_conversation_messages": "owner_id",
    "persona_memories": "owner_id",
    "audit_events": "owner_id",
    "persona_memory_relations": "owner_id",
    "persona_retrieval_runs": "owner_id",
    "persona_memory_embeddings": "owner_id",
}

_INDIRECT_OWNER_EXPRESSIONS = {
    "task_events": (
        "EXISTS (SELECT 1 FROM tasks parent "
        "WHERE parent.id = task_events.task_id)"
    ),
    "task_runs": (
        "EXISTS (SELECT 1 FROM tasks parent "
        "WHERE parent.id = task_runs.task_id)"
    ),
    "approvals": (
        "EXISTS (SELECT 1 FROM tasks parent "
        "WHERE parent.id = approvals.task_id)"
    ),
    "artifacts": (
        "EXISTS (SELECT 1 FROM tasks parent "
        "WHERE parent.id = artifacts.task_id)"
    ),
    "document_chunks": (
        "EXISTS (SELECT 1 FROM personas parent "
        "WHERE parent.id = document_chunks.persona_id)"
    ),
    "preference_evidence": (
        "EXISTS (SELECT 1 FROM preferences parent "
        "WHERE parent.id = preference_evidence.preference_id)"
    ),
    "tool_calls": (
        "EXISTS (SELECT 1 FROM task_runs parent "
        "WHERE parent.id = tool_calls.task_run_id)"
    ),
    "workflow_runs": (
        "EXISTS (SELECT 1 FROM task_runs parent "
        "WHERE parent.id = workflow_runs.task_run_id)"
    ),
    "decision_records": (
        "EXISTS (SELECT 1 FROM tasks parent "
        "WHERE parent.id = decision_records.task_id)"
    ),
    "feedback": (
        "EXISTS (SELECT 1 FROM tasks parent "
        "WHERE parent.id = feedback.task_id)"
    ),
    "persona_memory_versions": (
        "EXISTS (SELECT 1 FROM persona_memories parent "
        "WHERE parent.id = persona_memory_versions.memory_id)"
    ),
    "persona_memory_evidence": (
        "EXISTS (SELECT 1 FROM persona_memory_versions parent "
        "WHERE parent.id = persona_memory_evidence.memory_version_id)"
    ),
    "persona_model_calls": (
        "EXISTS (SELECT 1 FROM persona_retrieval_runs parent "
        "WHERE parent.id = persona_model_calls.retrieval_run_id)"
    ),
    "persona_answer_citations": (
        "EXISTS (SELECT 1 FROM persona_conversation_messages parent "
        "WHERE parent.id = persona_answer_citations.assistant_message_id)"
    ),
}


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    op.add_column(
        "users",
        sa.Column("username", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("password_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.String(length=40),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "status",
            sa.String(length=40),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=(
                sa.func.now()
                if dialect_name == "postgresql"
                else sa.text("'1970-01-01 00:00:00+00:00'")
            ),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE users SET updated_at = created_at "
            "WHERE updated_at = '1970-01-01 00:00:00+00:00'"
        )
    )
    op.create_index(
        op.f("ix_users_username"),
        "users",
        ["username"],
        unique=True,
    )
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    op.create_index(op.f("ix_users_status"), "users", ["status"], unique=False)

    if dialect_name == "postgresql":
        op.create_check_constraint(
            "ck_user_role",
            "users",
            "role IN ('legacy', 'admin', 'member')",
        )
        op.create_check_constraint(
            "ck_user_status",
            "users",
            "status IN ('legacy', 'active', 'disabled')",
        )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reauthenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=120), nullable=True),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "absolute_expires_at",
        "idle_expires_at",
        "revoked_at",
        "token_hash",
        "user_id",
    ):
        op.create_index(
            op.f(f"ix_auth_sessions_{column}"),
            "auth_sessions",
            [column],
            unique=column == "token_hash",
        )

    op.create_table(
        "auth_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("account_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "account_id",
        "action",
        "occurred_at",
        "outcome",
        "request_id",
    ):
        op.create_index(
            op.f(f"ix_auth_events_{column}"),
            "auth_events",
            [column],
            unique=False,
        )

    op.create_table(
        "legacy_owner_migrations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_owner_id", sa.String(length=64), nullable=False),
        sa.Column("target_user_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('applied', 'rolled_back')",
            name="ck_legacy_owner_migration_status",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "created_by_id",
        "source_owner_id",
        "status",
        "target_user_id",
    ):
        op.create_index(
            op.f(f"ix_legacy_owner_migrations_{column}"),
            "legacy_owner_migrations",
            [column],
            unique=False,
        )

    if dialect_name == "postgresql":
        _enable_postgresql_row_security()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _disable_postgresql_row_security()

    for column in (
        "target_user_id",
        "status",
        "source_owner_id",
        "created_by_id",
    ):
        op.drop_index(
            op.f(f"ix_legacy_owner_migrations_{column}"),
            table_name="legacy_owner_migrations",
        )
    op.drop_table("legacy_owner_migrations")

    for column in (
        "request_id",
        "outcome",
        "occurred_at",
        "action",
        "account_id",
    ):
        op.drop_index(
            op.f(f"ix_auth_events_{column}"),
            table_name="auth_events",
        )
    op.drop_table("auth_events")

    for column in (
        "user_id",
        "token_hash",
        "revoked_at",
        "idle_expires_at",
        "absolute_expires_at",
    ):
        op.drop_index(
            op.f(f"ix_auth_sessions_{column}"),
            table_name="auth_sessions",
        )
    op.drop_table("auth_sessions")

    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("ck_user_status", "users", type_="check")
        op.drop_constraint("ck_user_role", "users", type_="check")
    op.drop_index(op.f("ix_users_status"), table_name="users")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    for column in (
        "updated_at",
        "last_login_at",
        "password_changed_at",
        "locked_until",
        "failed_login_count",
        "status",
        "role",
        "password_hash",
        "username",
    ):
        op.drop_column("users", column)


def _enable_postgresql_row_security() -> None:
    bypass = "current_setting('personaos.system_bypass', true) = 'on'"
    owner = "NULLIF(current_setting('personaos.owner_id', true), '')"
    for table, column in _DIRECT_OWNER_COLUMNS.items():
        expression = f"({bypass} OR {column} = {owner})"
        _create_policy(table, expression)
    for table, expression in _INDIRECT_OWNER_EXPRESSIONS.items():
        _create_policy(table, f"({bypass} OR {expression})")


def _create_policy(table: str, expression: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY personaos_owner_isolation ON "{table}" '
        f"USING ({expression}) WITH CHECK ({expression})"
    )


def _disable_postgresql_row_security() -> None:
    tables = [
        *_INDIRECT_OWNER_EXPRESSIONS,
        *_DIRECT_OWNER_COLUMNS,
    ]
    for table in tables:
        op.execute(
            f'DROP POLICY IF EXISTS personaos_owner_isolation ON "{table}"'
        )
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
