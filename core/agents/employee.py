from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class EmployeeDefinition(BaseModel):
    employee_id: str
    name: str
    role: str
    description: str = ""
    goals: list[str] = Field(default_factory=list)
    allowed_permissions: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)
    approval_policy: dict[str, Literal["required", "automatic", "forbidden"]] = Field(
        default_factory=dict
    )


class EmployeeCatalog:
    def __init__(self, definitions: list[EmployeeDefinition]) -> None:
        self._definitions = {item.employee_id: item for item in definitions}

    @classmethod
    def from_directory(cls, directory: Path) -> EmployeeCatalog:
        definitions: list[EmployeeDefinition] = []
        for path in sorted(directory.glob("*.y*ml")):
            with path.open("r", encoding="utf-8") as stream:
                definitions.append(EmployeeDefinition.model_validate(yaml.safe_load(stream)))
        if not definitions:
            raise ValueError(f"No employee definitions found in {directory}")
        return cls(definitions)

    def get(self, employee_id: str) -> EmployeeDefinition:
        try:
            return self._definitions[employee_id]
        except KeyError as exc:
            raise KeyError(f"Unknown employee: {employee_id}") from exc

    def all(self) -> list[EmployeeDefinition]:
        return list(self._definitions.values())
