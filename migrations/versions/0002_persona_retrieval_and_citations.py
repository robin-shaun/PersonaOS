"""persona retrieval and citations

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-25 10:36:02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "embedding_spaces",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("distance_metric", sa.String(length=40), nullable=False),
        sa.Column("normalization", sa.String(length=40), nullable=False),
        sa.Column("document_template_version", sa.String(length=50), nullable=False),
        sa.Column("query_template_version", sa.String(length=50), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("data_boundary", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "data_boundary IN ('local', 'private_network', 'external')",
            name="ck_embedding_space_data_boundary",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'retired')",
            name="ck_embedding_space_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "model_name",
            "model_version",
            "dimensions",
            "distance_metric",
            "normalization",
            "document_template_version",
            "query_template_version",
            "config_hash",
            "data_boundary",
            name="uq_embedding_space_definition",
        ),
    )
    op.create_index(
        op.f("ix_embedding_spaces_provider"),
        "embedding_spaces",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_embedding_spaces_status"),
        "embedding_spaces",
        ["status"],
        unique=False,
    )

    op.create_table(
        "persona_conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("persona_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleted')",
            name="ck_persona_conversation_status",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("owner_id", "persona_id", "status"):
        op.create_index(
            op.f(f"ix_persona_conversations_{column}"),
            "persona_conversations",
            [column],
            unique=False,
        )

    op.create_table(
        "persona_conversation_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("persona_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("answer_status", sa.String(length=40), nullable=False),
        sa.Column("claims", sa.JSON(), nullable=False),
        sa.Column("uncertainty", sa.JSON(), nullable=False),
        sa.Column("simulation_notice", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "answer_status IN ('not_applicable', 'answered', 'no_memory')",
            name="ck_persona_message_answer_status",
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_persona_message_role",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["persona_conversations.id"],
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("conversation_id", "owner_id", "persona_id", "role"):
        op.create_index(
            op.f(f"ix_persona_conversation_messages_{column}"),
            "persona_conversation_messages",
            [column],
            unique=False,
        )

    op.create_table(
        "persona_retrieval_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("user_message_id", sa.String(length=36), nullable=False),
        sa.Column("persona_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("embedding_space_id", sa.String(length=64), nullable=False),
        sa.Column("query_sha256", sa.String(length=64), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('completed', 'no_evidence', 'failed')",
            name="ck_persona_retrieval_status",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["persona_conversations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["embedding_space_id"],
            ["embedding_spaces.id"],
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"]),
        sa.ForeignKeyConstraint(
            ["user_message_id"],
            ["persona_conversation_messages.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_message_id",
            name="uq_persona_retrieval_user_message",
        ),
    )
    for column in (
        "conversation_id",
        "embedding_space_id",
        "owner_id",
        "persona_id",
        "status",
        "user_message_id",
    ):
        op.create_index(
            op.f(f"ix_persona_retrieval_runs_{column}"),
            "persona_retrieval_runs",
            [column],
            unique=False,
        )

    op.create_table(
        "persona_memory_embeddings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("memory_id", sa.String(length=36), nullable=False),
        sa.Column("memory_version_id", sa.String(length=36), nullable=False),
        sa.Column("persona_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("embedding_space_id", sa.String(length=64), nullable=False),
        sa.Column(
            "embedding",
            sa.JSON().with_variant(VECTOR(), "postgresql"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["embedding_space_id"],
            ["embedding_spaces.id"],
        ),
        sa.ForeignKeyConstraint(["memory_id"], ["persona_memories.id"]),
        sa.ForeignKeyConstraint(
            ["memory_version_id"],
            ["persona_memory_versions.id"],
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "memory_version_id",
            "embedding_space_id",
            name="uq_persona_memory_embedding_space",
        ),
    )
    for column in (
        "embedding_space_id",
        "memory_id",
        "memory_version_id",
        "owner_id",
        "persona_id",
    ):
        op.create_index(
            op.f(f"ix_persona_memory_embeddings_{column}"),
            "persona_memory_embeddings",
            [column],
            unique=False,
        )

    op.create_table(
        "persona_model_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_run_id", sa.String(length=36), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=False),
        sa.Column("prompt_template_version", sa.String(length=50), nullable=False),
        sa.Column("data_boundary", sa.String(length=40), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("error_type", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "data_boundary IN ('local', 'private_network', 'external')",
            name="ck_persona_model_call_data_boundary",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'failed', 'skipped')",
            name="ck_persona_model_call_status",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["persona_conversation_messages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_run_id"],
            ["persona_retrieval_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("assistant_message_id", "retrieval_run_id", "status"):
        op.create_index(
            op.f(f"ix_persona_model_calls_{column}"),
            "persona_model_calls",
            [column],
            unique=False,
        )

    op.create_table(
        "persona_answer_citations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_run_id", sa.String(length=36), nullable=False),
        sa.Column("citation_id", sa.String(length=20), nullable=False),
        sa.Column("claim_indexes", sa.JSON(), nullable=False),
        sa.Column("memory_id", sa.String(length=36), nullable=False),
        sa.Column("memory_version_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("document_chunk_id", sa.String(length=36), nullable=False),
        sa.Column("locator_snapshot", sa.JSON(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["persona_conversation_messages.id"],
        ),
        sa.ForeignKeyConstraint(["document_chunk_id"], ["document_chunks.id"]),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["persona_memory_evidence.id"],
        ),
        sa.ForeignKeyConstraint(["memory_id"], ["persona_memories.id"]),
        sa.ForeignKeyConstraint(
            ["memory_version_id"],
            ["persona_memory_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_run_id"],
            ["persona_retrieval_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assistant_message_id",
            "citation_id",
            name="uq_persona_answer_citation",
        ),
    )
    for column in (
        "assistant_message_id",
        "document_chunk_id",
        "evidence_id",
        "memory_id",
        "memory_version_id",
        "retrieval_run_id",
        "source_document_id",
    ):
        op.create_index(
            op.f(f"ix_persona_answer_citations_{column}"),
            "persona_answer_citations",
            [column],
            unique=False,
        )


def downgrade() -> None:
    _drop_table_with_indexes(
        "persona_answer_citations",
        (
            "assistant_message_id",
            "document_chunk_id",
            "evidence_id",
            "memory_id",
            "memory_version_id",
            "retrieval_run_id",
            "source_document_id",
        ),
    )
    _drop_table_with_indexes(
        "persona_model_calls",
        ("assistant_message_id", "retrieval_run_id", "status"),
    )
    _drop_table_with_indexes(
        "persona_memory_embeddings",
        (
            "embedding_space_id",
            "memory_id",
            "memory_version_id",
            "owner_id",
            "persona_id",
        ),
    )
    _drop_table_with_indexes(
        "persona_retrieval_runs",
        (
            "conversation_id",
            "embedding_space_id",
            "owner_id",
            "persona_id",
            "status",
            "user_message_id",
        ),
    )
    _drop_table_with_indexes(
        "persona_conversation_messages",
        ("conversation_id", "owner_id", "persona_id", "role"),
    )
    _drop_table_with_indexes(
        "persona_conversations",
        ("owner_id", "persona_id", "status"),
    )
    _drop_table_with_indexes(
        "embedding_spaces",
        ("provider", "status"),
    )


def _drop_table_with_indexes(table_name: str, columns: Sequence[str]) -> None:
    for column in reversed(columns):
        op.drop_index(op.f(f"ix_{table_name}_{column}"), table_name=table_name)
    op.drop_table(table_name)
