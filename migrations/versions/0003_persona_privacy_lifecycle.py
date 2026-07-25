"""persona privacy lifecycle

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-25 12:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "personas",
        sa.Column(
            "allowed_model_boundaries",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("""'["local"]'"""),
        ),
    )
    op.create_table(
        "persona_memory_relations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("persona_id", sa.String(length=36), nullable=False),
        sa.Column("from_memory_id", sa.String(length=36), nullable=False),
        sa.Column("to_memory_id", sa.String(length=36), nullable=False),
        sa.Column("relation", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_memory_version_ids", sa.JSON(), nullable=False),
        sa.Column("created_by_type", sa.String(length=40), nullable=False),
        sa.Column("created_by_id", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_persona_memory_relation_confidence",
        ),
        sa.CheckConstraint(
            "from_memory_id <> to_memory_id",
            name="ck_persona_memory_relation_not_self",
        ),
        sa.CheckConstraint(
            "relation IN ('supports', 'conflicts', 'derived_from', "
            "'supersedes', 'related_to')",
            name="ck_persona_memory_relation_type",
        ),
        sa.ForeignKeyConstraint(["from_memory_id"], ["persona_memories.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"]),
        sa.ForeignKeyConstraint(["to_memory_id"], ["persona_memories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_memory_id",
            "to_memory_id",
            "relation",
            name="uq_persona_memory_relation",
        ),
    )
    for column in (
        "from_memory_id",
        "owner_id",
        "persona_id",
        "relation",
        "to_memory_id",
    ):
        op.create_index(
            op.f(f"ix_persona_memory_relations_{column}"),
            "persona_memory_relations",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in (
        "to_memory_id",
        "relation",
        "persona_id",
        "owner_id",
        "from_memory_id",
    ):
        op.drop_index(
            op.f(f"ix_persona_memory_relations_{column}"),
            table_name="persona_memory_relations",
        )
    op.drop_table("persona_memory_relations")
    op.drop_column("personas", "allowed_model_boundaries")
