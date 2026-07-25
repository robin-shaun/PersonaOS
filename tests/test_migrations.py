from __future__ import annotations

from pathlib import Path
from runpy import run_path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from core.storage.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_every_owner_sensitive_table_has_a_row_security_policy() -> None:
    migration = run_path(
        str(
            PROJECT_ROOT
            / "migrations"
            / "versions"
            / "0004_trusted_local_accounts.py"
        )
    )
    protected_tables = set(migration["_DIRECT_OWNER_COLUMNS"]) | set(
        migration["_INDIRECT_OWNER_EXPRESSIONS"]
    )
    deliberately_global_tables = {
        "embedding_spaces",
        "employees",
        "skill_versions",
        "skills",
        "workflows",
    }

    assert set(Base.metadata.tables) == (
        protected_tables | deliberately_global_tables
    )
    assert not protected_tables & deliberately_global_tables


def test_initial_migration_matches_metadata(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DIGITAL_EMPLOYEE_DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "alembic_version",
        "auth_events",
        "auth_sessions",
        "audit_events",
        "document_chunks",
        "embedding_spaces",
        "legacy_owner_migrations",
        "persona_answer_citations",
        "persona_conversation_messages",
        "persona_conversations",
        "persona_memory_embeddings",
        "persona_model_calls",
        "persona_memories",
        "persona_memory_evidence",
        "persona_memory_relations",
        "persona_memory_versions",
        "persona_retrieval_runs",
        "personas",
        "queue_jobs",
        "source_documents",
        "tasks",
    } <= tables
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        )
    assert "allowed_model_boundaries" in {
        item["name"] for item in inspector.get_columns("personas")
    }
    assert {
        "username",
        "password_hash",
        "role",
        "status",
        "updated_at",
    } <= {item["name"] for item in inspector.get_columns("users")}

    command.check(config)
    command.downgrade(config, "base")
    assert "personas" not in set(inspect(engine).get_table_names())
    engine.dispose()
