from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    base_dir: Path
    database_url: str
    employee_config_dir: Path
    skill_config_dir: Path
    workflow_config_dir: Path
    github_token: str | None
    github_api_url: str
    runtime_name: str
    api_host: str = "127.0.0.1"
    api_port: int = 18110
    queue_max_attempts: int = 3
    worker_poll_interval_seconds: float = 1.0
    worker_lease_seconds: int = 300
    worker_retry_delay_seconds: float = 5.0
    worker_task_timeout_seconds: float = 300.0
    worker_control_poll_seconds: float = 0.25

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "Settings":
        project_root = (base_dir or Path(__file__).resolve().parents[1]).resolve()
        default_db = f"sqlite:///{project_root / 'var' / 'digital_employee.db'}"
        configured_db = os.getenv("DIGITAL_EMPLOYEE_DATABASE_URL") or default_db
        return cls(
            base_dir=project_root,
            database_url=configured_db,
            employee_config_dir=project_root / "data" / "employee_templates",
            skill_config_dir=project_root / "data" / "skills",
            workflow_config_dir=project_root / "data" / "workflows",
            github_token=os.getenv("GITHUB_TOKEN") or None,
            github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
            runtime_name=os.getenv("DIGITAL_EMPLOYEE_RUNTIME", "rules"),
            api_host=os.getenv("DIGITAL_EMPLOYEE_API_HOST", "127.0.0.1"),
            api_port=_env_int(
                "DIGITAL_EMPLOYEE_API_PORT",
                default=18110,
                minimum=1,
                maximum=65535,
            ),
            queue_max_attempts=_env_int(
                "DIGITAL_EMPLOYEE_QUEUE_MAX_ATTEMPTS",
                default=3,
                minimum=1,
                maximum=20,
            ),
            worker_poll_interval_seconds=_env_float(
                "DIGITAL_EMPLOYEE_WORKER_POLL_SECONDS",
                default=1.0,
                minimum=0.05,
            ),
            worker_lease_seconds=_env_int(
                "DIGITAL_EMPLOYEE_WORKER_LEASE_SECONDS",
                default=300,
                minimum=5,
                maximum=86400,
            ),
            worker_retry_delay_seconds=_env_float(
                "DIGITAL_EMPLOYEE_WORKER_RETRY_DELAY_SECONDS",
                default=5.0,
                minimum=0.0,
            ),
            worker_task_timeout_seconds=_env_float(
                "DIGITAL_EMPLOYEE_WORKER_TASK_TIMEOUT_SECONDS",
                default=300.0,
                minimum=0.05,
            ),
            worker_control_poll_seconds=_env_float(
                "DIGITAL_EMPLOYEE_WORKER_CONTROL_POLL_SECONDS",
                default=0.25,
                minimum=0.01,
            ),
        )


def _env_int(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_float(
    name: str,
    *,
    default: float,
    minimum: float,
) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value
