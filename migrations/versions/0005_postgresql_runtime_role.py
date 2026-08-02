"""run ordinary PostgreSQL transactions under a constrained role

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-25 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POSTGRES_RUNTIME_ROLE = "personaos_runtime"

_RUNTIME_TABLES = (
    "approvals",
    "artifacts",
    "audit_events",
    "auth_events",
    "auth_sessions",
    "decision_records",
    "document_chunks",
    "embedding_spaces",
    "employee_assignments",
    "employees",
    "feedback",
    "github_connections",
    "legacy_owner_migrations",
    "memory_sources",
    "persona_answer_citations",
    "persona_conversation_messages",
    "persona_conversations",
    "persona_memories",
    "persona_memory_embeddings",
    "persona_memory_evidence",
    "persona_memory_relations",
    "persona_memory_versions",
    "persona_model_calls",
    "persona_retrieval_runs",
    "personas",
    "preference_evidence",
    "preference_reviews",
    "preferences",
    "queue_jobs",
    "skill_versions",
    "skills",
    "source_documents",
    "task_events",
    "task_runs",
    "tasks",
    "tool_calls",
    "users",
    "workflow_runs",
    "workflows",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            """
            DO $personaos$
            DECLARE
                existing_role pg_roles%ROWTYPE;
            BEGIN
                SELECT *
                INTO existing_role
                FROM pg_roles
                WHERE rolname = 'personaos_runtime';

                IF NOT FOUND THEN
                    CREATE ROLE personaos_runtime
                        NOLOGIN
                        NOSUPERUSER
                        NOCREATEDB
                        NOCREATEROLE
                        NOINHERIT
                        NOREPLICATION
                        NOBYPASSRLS;
                ELSIF
                    existing_role.rolcanlogin
                    OR existing_role.rolsuper
                    OR existing_role.rolcreatedb
                    OR existing_role.rolcreaterole
                    OR existing_role.rolinherit
                    OR existing_role.rolreplication
                    OR existing_role.rolbypassrls
                    OR EXISTS (
                        SELECT 1
                        FROM pg_auth_members
                        WHERE member = existing_role.oid
                    )
                THEN
                    RAISE EXCEPTION
                        'existing personaos_runtime role is not constrained';
                END IF;
            END
            $personaos$;
            """
        )
    )
    op.execute(
        sa.text(
            f'GRANT USAGE ON SCHEMA public TO "{POSTGRES_RUNTIME_ROLE}"'
        )
    )
    for table in _RUNTIME_TABLES:
        op.execute(
            sa.text(
                "GRANT SELECT, INSERT, UPDATE, DELETE "
                f'ON TABLE "{table}" TO "{POSTGRES_RUNTIME_ROLE}"'
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table in reversed(_RUNTIME_TABLES):
        op.execute(
            sa.text(
                "REVOKE SELECT, INSERT, UPDATE, DELETE "
                f'ON TABLE "{table}" FROM "{POSTGRES_RUNTIME_ROLE}"'
            )
        )
    op.execute(
        sa.text(
            f'REVOKE USAGE ON SCHEMA public FROM "{POSTGRES_RUNTIME_ROLE}"'
        )
    )
    op.execute(sa.text(f'DROP ROLE "{POSTGRES_RUNTIME_ROLE}"'))
