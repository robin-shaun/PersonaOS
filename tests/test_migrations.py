from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
        "audit_events",
        "document_chunks",
        "embedding_spaces",
        "persona_answer_citations",
        "persona_conversation_messages",
        "persona_conversations",
        "persona_memory_embeddings",
        "persona_model_calls",
        "persona_memories",
        "persona_memory_evidence",
        "persona_memory_versions",
        "persona_retrieval_runs",
        "personas",
        "queue_jobs",
        "source_documents",
        "tasks",
    } <= tables
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
        )

    command.check(config)
    command.downgrade(config, "base")
    assert "personas" not in set(inspect(engine).get_table_names())
    engine.dispose()
