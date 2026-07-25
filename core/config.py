from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    base_dir: Path
    database_url: str
    employee_config_dir: Path
    skill_config_dir: Path
    workflow_config_dir: Path
    github_token: str | None = field(repr=False)
    github_api_url: str
    runtime_name: str
    github_api_version: str = "2026-03-10"
    github_app_id: str | None = None
    github_app_private_key: str | None = field(default=None, repr=False)
    hermes_api_url: str = "http://127.0.0.1:8642"
    hermes_api_key: str | None = field(default=None, repr=False)
    hermes_model: str = "hermes-agent"
    hermes_request_timeout_seconds: float = 20.0
    hermes_poll_interval_seconds: float = 0.5
    hermes_max_context_bytes: int = 1_000_000
    api_host: str = "127.0.0.1"
    api_port: int = 18110
    queue_max_attempts: int = 3
    worker_poll_interval_seconds: float = 1.0
    worker_lease_seconds: int = 300
    worker_retry_delay_seconds: float = 5.0
    worker_task_timeout_seconds: float = 300.0
    worker_control_poll_seconds: float = 0.25
    database_auto_create_schema: bool = True
    persona_local_owner_id: str = "local-user"
    persona_blob_dir: Path | None = None
    persona_blob_key: str | None = field(default=None, repr=False)
    persona_blob_key_path: Path | None = field(default=None, repr=False)
    persona_max_upload_bytes: int = 5 * 1024 * 1024

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> Settings:
        project_root = (base_dir or Path(__file__).resolve().parents[1]).resolve()
        default_db = f"sqlite:///{project_root / 'var' / 'digital_employee.db'}"
        configured_db = os.getenv("DIGITAL_EMPLOYEE_DATABASE_URL") or default_db
        github_app_id = (os.getenv("GITHUB_APP_ID") or "").strip() or None
        github_app_private_key = _github_app_private_key(project_root)
        if bool(github_app_id) != bool(github_app_private_key):
            raise ValueError(
                "GITHUB_APP_ID and a GitHub App private key must be configured together"
            )
        persona_owner_id = (
            os.getenv("PERSONA_LOCAL_OWNER_ID") or "local-user"
        ).strip()
        if not persona_owner_id:
            raise ValueError("PERSONA_LOCAL_OWNER_ID must not be empty")
        persona_blob_key = (os.getenv("PERSONA_BLOB_KEY") or "").strip() or None
        persona_blob_key_path = _configured_path(
            project_root,
            os.getenv("PERSONA_BLOB_KEY_PATH"),
        )
        if persona_blob_key and persona_blob_key_path:
            raise ValueError(
                "Configure only one of PERSONA_BLOB_KEY or PERSONA_BLOB_KEY_PATH"
            )
        return cls(
            base_dir=project_root,
            database_url=configured_db,
            employee_config_dir=project_root / "data" / "employee_templates",
            skill_config_dir=project_root / "data" / "skills",
            workflow_config_dir=project_root / "data" / "workflows",
            github_token=os.getenv("GITHUB_TOKEN") or None,
            github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
            runtime_name=os.getenv("DIGITAL_EMPLOYEE_RUNTIME", "rules")
            .strip()
            .lower(),
            github_api_version=os.getenv(
                "GITHUB_API_VERSION", "2026-03-10"
            ).strip(),
            github_app_id=github_app_id,
            github_app_private_key=github_app_private_key,
            hermes_api_url=(
                os.getenv("HERMES_API_URL") or "http://127.0.0.1:8642"
            )
            .strip()
            .rstrip("/"),
            hermes_api_key=(os.getenv("HERMES_API_KEY") or "").strip() or None,
            hermes_model=(os.getenv("HERMES_MODEL") or "hermes-agent").strip(),
            hermes_request_timeout_seconds=_env_float(
                "HERMES_REQUEST_TIMEOUT_SECONDS",
                default=20.0,
                minimum=0.1,
            ),
            hermes_poll_interval_seconds=_env_float(
                "HERMES_POLL_INTERVAL_SECONDS",
                default=0.5,
                minimum=0.05,
            ),
            hermes_max_context_bytes=_env_int(
                "HERMES_MAX_CONTEXT_BYTES",
                default=1_000_000,
                minimum=1_000,
                maximum=10_000_000,
            ),
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
            database_auto_create_schema=_env_bool(
                "DIGITAL_EMPLOYEE_AUTO_CREATE_SCHEMA",
                default=True,
            ),
            persona_local_owner_id=persona_owner_id,
            persona_blob_dir=_configured_path(
                project_root,
                os.getenv("PERSONA_BLOB_DIR"),
            ),
            persona_blob_key=persona_blob_key,
            persona_blob_key_path=persona_blob_key_path,
            persona_max_upload_bytes=_env_int(
                "PERSONA_MAX_UPLOAD_BYTES",
                default=5 * 1024 * 1024,
                minimum=1,
                maximum=100 * 1024 * 1024,
            ),
        )

    @property
    def github_app_configured(self) -> bool:
        return bool(self.github_app_id and self.github_app_private_key)


def _github_app_private_key(project_root: Path) -> str | None:
    inline_key = os.getenv("GITHUB_APP_PRIVATE_KEY")
    key_path = (os.getenv("GITHUB_APP_PRIVATE_KEY_PATH") or "").strip()
    if inline_key and key_path:
        raise ValueError(
            "Configure only one of GITHUB_APP_PRIVATE_KEY or "
            "GITHUB_APP_PRIVATE_KEY_PATH"
        )
    if inline_key:
        normalized = inline_key.strip()
        if "\\n" in normalized and "\n" not in normalized:
            normalized = normalized.replace("\\n", "\n")
        return normalized or None
    if not key_path:
        return None
    path = Path(key_path).expanduser()
    if not path.is_absolute():
        path = project_root / path
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        raise ValueError(f"Unable to read GITHUB_APP_PRIVATE_KEY_PATH: {path}") from exc


def _configured_path(project_root: Path, raw_value: str | None) -> Path | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


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


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")
