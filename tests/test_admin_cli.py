from __future__ import annotations

import io
import json

import pytest
from sqlalchemy import create_engine, text

from apps.admin import run


def test_trusted_cli_bootstraps_admin_without_echoing_or_argv_password(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    password = "trusted-cli-password-123"
    monkeypatch.setenv("DIGITAL_EMPLOYEE_DATABASE_URL", database_url)
    monkeypatch.setenv("DIGITAL_EMPLOYEE_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv(
        "PERSONA_AUTH_KEY_PATH",
        str(tmp_path / "auth.key"),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{password}\n"))

    exit_code = run(
        [
            "create-account",
            "--username",
            "local-admin",
            "--display-name",
            "Local Admin",
            "--role",
            "admin",
            "--password-stdin",
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert password not in output.out
    assert password not in output.err
    payload = json.loads(output.out)
    assert payload["created"] is True
    assert payload["account"]["username"] == "local-admin"
    assert "password_hash" not in payload["account"]

    engine = create_engine(database_url)
    with engine.connect() as connection:
        password_hash = connection.scalar(
            text(
                "SELECT password_hash FROM users "
                "WHERE username = 'local-admin'"
            )
        )
    engine.dispose()
    assert password_hash.startswith("$argon2id$")
    assert password not in password_hash


def test_admin_cli_rejects_password_command_line_argument() -> None:
    with pytest.raises(SystemExit):
        run(
            [
                "create-account",
                "--username",
                "local-admin",
                "--display-name",
                "Local Admin",
                "--role",
                "admin",
                "--password",
                "must-not-be-supported",
            ]
        )
