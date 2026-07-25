from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class WorkflowCondition(BaseModel):
    path: str
    equals: Any


class WorkflowStepDefinition(BaseModel):
    id: str
    uses: str
    retries: int = Field(default=0, ge=0, le=5)
    when: WorkflowCondition | None = None


class WorkflowDefinition(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    steps: list[WorkflowStepDefinition]


class WorkflowCatalog:
    def __init__(self, definitions: list[WorkflowDefinition]) -> None:
        self._definitions = {item.name: item for item in definitions}

    @classmethod
    def from_directory(cls, directory: Path) -> WorkflowCatalog:
        definitions: list[WorkflowDefinition] = []
        for path in sorted(directory.glob("*.y*ml")):
            with path.open("r", encoding="utf-8") as stream:
                definitions.append(WorkflowDefinition.model_validate(yaml.safe_load(stream)))
        if not definitions:
            raise ValueError(f"No workflow definitions found in {directory}")
        return cls(definitions)

    def get(self, name: str) -> WorkflowDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise KeyError(f"Unknown workflow: {name}") from exc

    def all(self) -> list[WorkflowDefinition]:
        return list(self._definitions.values())
