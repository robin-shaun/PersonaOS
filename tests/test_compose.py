from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_compose_is_local_first_and_runs_migrations() -> None:
    compose = yaml.safe_load(
        (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    )
    services = compose["services"]

    assert set(services) == {"api", "db", "worker"}
    assert services["db"]["image"] == ("pgvector/pgvector:0.8.5-pg17-bookworm")
    assert services["api"]["ports"] == ["127.0.0.1:18110:18110"]
    assert services["api"]["image"] == "personaos:0.8.0"
    assert services["worker"]["image"] == services["api"]["image"]
    assert "alembic upgrade head" in services["api"]["command"][-1]
    assert (
        services["api"]["environment"]["DIGITAL_EMPLOYEE_AUTO_CREATE_SCHEMA"] == "false"
    )
    assert services["worker"]["depends_on"]["api"]["condition"] == ("service_healthy")
    assert services["api"]["volumes"] == ["personaos-private:/app/var"]
    assert services["worker"]["volumes"] == ["personaos-private:/app/var"]
    assert services["api"]["environment"]["PERSONA_EMBEDDING_DIMENSIONS"] == "384"
