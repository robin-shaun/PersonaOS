from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_compose_is_local_first_and_runs_migrations() -> None:
    compose = yaml.safe_load(
        (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    )
    services = compose["services"]

    assert set(services) == {"api", "db", "web", "worker"}
    assert services["db"]["image"] == (
        "pgvector/pgvector:0.8.5-pg17-bookworm@"
        "sha256:d2ef61f42ef767baa5a1475393303cc235bcd92febd9d7014eddb48b41f3bad0"
    )
    assert services["api"]["ports"] == ["127.0.0.1:18110:18110"]
    assert services["api"]["image"] == "personaos:0.12.0"
    assert services["worker"]["image"] == services["api"]["image"]
    assert services["web"]["image"] == "personaos-web:0.12.0"
    assert services["web"]["ports"] == [
        "${PERSONAOS_WEB_BIND_HOST:-127.0.0.1}:18111:8080"
    ]
    assert services["web"]["build"]["dockerfile"] == "apps/web/Dockerfile"
    assert "alembic upgrade head" in services["api"]["command"][-1]
    assert (
        services["api"]["environment"]["DIGITAL_EMPLOYEE_AUTO_CREATE_SCHEMA"] == "false"
    )
    assert services["worker"]["depends_on"]["api"]["condition"] == ("service_healthy")
    assert services["web"]["depends_on"]["api"]["condition"] == ("service_healthy")
    assert services["web"]["healthcheck"]["test"][-1] == (
        "http://127.0.0.1:8080/healthz"
    )
    assert services["api"]["volumes"] == ["personaos-private:/app/var"]
    assert services["worker"]["volumes"] == ["personaos-private:/app/var"]
    assert "volumes" not in services["web"]
    assert services["api"]["environment"]["PERSONA_EMBEDDING_DIMENSIONS"] == "384"
    assert services["api"]["environment"]["PERSONA_MAX_EXPORT_BYTES"] == "26214400"
    assert services["api"]["environment"]["PERSONA_AUTH_KEY_PATH"] == (
        "/app/var/persona/auth.key"
    )
    assert services["api"]["environment"]["PERSONA_COOKIE_SECURE"] == (
        "${PERSONA_COOKIE_SECURE:-false}"
    )
    assert services["api"]["environment"][
        "PERSONA_PUBLIC_REGISTRATION_ENABLED"
    ] == "${PERSONA_PUBLIC_REGISTRATION_ENABLED:-false}"
    assert services["api"]["environment"]["PERSONA_TURNSTILE_SITE_KEY"] == (
        "${PERSONA_TURNSTILE_SITE_KEY:-}"
    )
    assert services["api"]["environment"][
        "PERSONA_TURNSTILE_SECRET_KEY"
    ] == "${PERSONA_TURNSTILE_SECRET_KEY:-}"
