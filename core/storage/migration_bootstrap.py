from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from core.config import Settings

_M1_PERSONA_TABLES = {
    "audit_events",
    "document_chunks",
    "persona_memories",
    "persona_memory_evidence",
    "persona_memory_versions",
    "personas",
    "source_documents",
}
_M2_PERSONA_TABLES = _M1_PERSONA_TABLES | {
    "embedding_spaces",
    "persona_answer_citations",
    "persona_conversation_messages",
    "persona_conversations",
    "persona_memory_embeddings",
    "persona_model_calls",
    "persona_retrieval_runs",
}
_M4_AUTH_TABLES = {
    "auth_events",
    "auth_sessions",
    "legacy_owner_migrations",
}
_M4_USER_COLUMNS = {
    "failed_login_count",
    "last_login_at",
    "locked_until",
    "password_changed_at",
    "password_hash",
    "role",
    "status",
    "updated_at",
    "username",
}


def prepare_startup_database(
    settings: Settings,
    *,
    project_root: Path,
) -> str:
    """Bring fresh or recognized versioned/M1/M2 databases to Alembic head.

    Legacy pre-persona create_all databases remain on their compatibility path.
    An unversioned persona schema is stamped only when its milestone signature is
    unambiguous; Alembic check then verifies the complete resulting metadata.
    """

    _ensure_sqlite_parent(settings.database_url)
    engine = create_engine(settings.database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if "alembic_version" in tables:
            _upgrade_and_check(project_root, settings.database_url)
            return "migrated"
        if not tables:
            _upgrade_and_check(project_root, settings.database_url)
            return "migrated"
        if "personas" not in tables:
            return "legacy-create-all"
        if engine.dialect.name != "sqlite":
            raise RuntimeError(
                "Refusing to stamp an unversioned non-SQLite persona database; "
                "back it up and run an explicit Alembic migration."
            )

        revision = _recognized_persona_revision(inspector, tables)
    finally:
        engine.dispose()

    _stamp_upgrade_and_check(
        project_root,
        settings.database_url,
        revision=revision,
    )
    return "migrated"


def _ensure_sqlite_parent(database_url: str) -> None:
    """Create the local database directory before SQLite opens a fresh file."""

    url = make_url(database_url)
    database = url.database
    if (
        url.get_backend_name() != "sqlite"
        or not database
        or database == ":memory:"
        or database.startswith("file:")
    ):
        return
    Path(database).expanduser().resolve().parent.mkdir(
        mode=0o700,
        parents=True,
        exist_ok=True,
    )


def _recognized_persona_revision(inspector, tables: set[str]) -> str:
    if not _M1_PERSONA_TABLES <= tables:
        raise RuntimeError(
            "Unversioned persona database does not match a recognized M1 schema."
        )
    m2_only_tables = _M2_PERSONA_TABLES - _M1_PERSONA_TABLES
    present_m2_tables = tables & m2_only_tables
    if not present_m2_tables:
        return "0001"
    if present_m2_tables != m2_only_tables:
        raise RuntimeError(
            "Unversioned persona database contains a partial M2 schema; "
            "refusing to guess a migration revision."
        )

    persona_columns = {item["name"] for item in inspector.get_columns("personas")}
    has_policy = "allowed_model_boundaries" in persona_columns
    has_relations = "persona_memory_relations" in tables
    if has_policy and has_relations:
        present_auth_tables = tables & _M4_AUTH_TABLES
        user_columns = {item["name"] for item in inspector.get_columns("users")}
        present_auth_columns = user_columns & _M4_USER_COLUMNS
        if (
            present_auth_tables == _M4_AUTH_TABLES
            and present_auth_columns == _M4_USER_COLUMNS
        ):
            return "0004"
        if not present_auth_tables and not present_auth_columns:
            return "0003"
        raise RuntimeError(
            "Unversioned persona database contains a partial M4 schema; "
            "refusing to guess a migration revision."
        )
    if not has_policy and not has_relations:
        return "0002"
    raise RuntimeError(
        "Unversioned persona database contains a partial M3 schema; "
        "refusing to guess a migration revision."
    )


def _upgrade_and_check(project_root: Path, database_url: str) -> None:
    config = _alembic_config(project_root, database_url)
    command.upgrade(config, "head")
    _check_without_stdout(config)


def _stamp_upgrade_and_check(
    project_root: Path,
    database_url: str,
    *,
    revision: str,
) -> None:
    config = _alembic_config(project_root, database_url)
    command.stamp(config, revision)
    command.upgrade(config, "head")
    _check_without_stdout(config)


def _check_without_stdout(config: Config) -> None:
    # start.sh consumes stdout as a one-line mode protocol. Alembic's successful
    # check writes a human message to stdout, while migration logs stay on stderr.
    command.check(config)


def _alembic_config(project_root: Path, database_url: str) -> Config:
    os.environ["DIGITAL_EMPLOYEE_DATABASE_URL"] = database_url
    config = Config(
        str(project_root / "alembic.ini"),
        stdout=StringIO(),
    )
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=False)
    settings = Settings.from_env(project_root)
    print(
        prepare_startup_database(
            settings,
            project_root=project_root,
        )
    )


if __name__ == "__main__":
    main()
