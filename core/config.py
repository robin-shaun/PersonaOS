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
        )

