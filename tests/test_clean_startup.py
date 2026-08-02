from __future__ import annotations

from pathlib import Path

from core.config import Settings
from core.storage.migration_bootstrap import prepare_startup_database

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_fresh_sqlite_startup_creates_missing_runtime_directory(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "new-runtime" / "nested" / "personaos.db"
    monkeypatch.setenv(
        "DIGITAL_EMPLOYEE_DATABASE_URL",
        f"sqlite:///{database_path}",
    )
    settings = Settings.from_env(PROJECT_ROOT)

    mode = prepare_startup_database(settings, project_root=PROJECT_ROOT)

    assert mode == "migrated"
    assert database_path.is_file()
