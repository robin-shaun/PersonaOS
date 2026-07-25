from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from core.config import Settings
from core.storage.database import Database
from core.storage.migration_bootstrap import main, prepare_startup_database

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_startup_bootstrap_migrates_a_fresh_database(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'fresh.db'}"
    settings = _settings(monkeypatch, database_url)

    mode = prepare_startup_database(settings, project_root=PROJECT_ROOT)

    assert mode == "migrated"
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        )
    assert "persona_memory_relations" in inspect(engine).get_table_names()
    engine.dispose()


def test_startup_bootstrap_cli_has_one_line_mode_protocol(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("DIGITAL_EMPLOYEE_DATABASE_URL", database_url)

    main()

    assert capsys.readouterr().out == "migrated\n"


def test_startup_bootstrap_recognizes_unversioned_m2_and_upgrades(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'm2.db'}"
    settings = _settings(monkeypatch, database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "0002")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, display_name, created_at) "
                "VALUES ('local-user', 'Local User', '2026-07-25T00:00:00Z')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO personas ("
                "id, owner_id, display_name, description, simulation_notice, "
                "status, version, created_at, updated_at"
                ") VALUES ("
                "'persona-before-m3', 'local-user', 'Existing Persona', '', "
                "'simulation', 'active', 1, "
                "'2026-07-25T00:00:00Z', '2026-07-25T00:00:00Z'"
                ")"
            )
        )
        connection.execute(text("DROP TABLE alembic_version"))

    mode = prepare_startup_database(settings, project_root=PROJECT_ROOT)

    assert mode == "migrated"
    columns = {item["name"] for item in inspect(engine).get_columns("personas")}
    assert "allowed_model_boundaries" in columns
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        )
        policy = connection.scalar(
            text(
                "SELECT allowed_model_boundaries FROM personas "
                "WHERE id = 'persona-before-m3'"
            )
        )
        assert json.loads(policy) == ["local"]
    engine.dispose()


def test_startup_bootstrap_keeps_pre_persona_compatibility_mode(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    settings = _settings(monkeypatch, database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE legacy_tasks (id TEXT PRIMARY KEY)"))

    mode = prepare_startup_database(settings, project_root=PROJECT_ROOT)

    assert mode == "legacy-create-all"
    assert "alembic_version" not in inspect(engine).get_table_names()
    engine.dispose()


def test_startup_bootstrap_rejects_partial_unversioned_m3(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'partial.db'}"
    settings = _settings(monkeypatch, database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "0002")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
        connection.execute(
            text(
                "ALTER TABLE personas ADD COLUMN "
                "allowed_model_boundaries JSON NOT NULL DEFAULT '[\"local\"]'"
            )
        )

    with pytest.raises(RuntimeError, match="partial M3"):
        prepare_startup_database(settings, project_root=PROJECT_ROOT)
    engine.dispose()


def test_startup_bootstrap_recognizes_unversioned_current_schema(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'current-create-all.db'}"
    settings = _settings(monkeypatch, database_url)
    database = Database(database_url)
    database.create_schema()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users ("
                "id, display_name, role, status, failed_login_count, "
                "created_at, updated_at"
                ") VALUES ("
                "'legacy', 'Legacy', 'legacy', 'legacy', 0, "
                "'2026-07-25T00:00:00Z', '2026-07-25T00:00:00Z'"
                ")"
            )
        )
    database.engine.dispose()

    mode = prepare_startup_database(settings, project_root=PROJECT_ROOT)

    assert mode == "migrated"
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0004"
        )
        assert (
            connection.scalar(
                text("SELECT status FROM users WHERE id = 'legacy'")
            )
            == "legacy"
        )
    engine.dispose()


def _settings(monkeypatch, database_url: str) -> Settings:
    monkeypatch.setenv("DIGITAL_EMPLOYEE_DATABASE_URL", database_url)
    return Settings.from_env(PROJECT_ROOT)
