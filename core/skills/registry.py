from __future__ import annotations

from pathlib import Path

import yaml

from core.skills.models import SkillDefinition


class SkillRegistry:
    def __init__(self, definitions: list[SkillDefinition]) -> None:
        self._definitions = {item.name: item for item in definitions}

    @classmethod
    def from_directory(cls, directory: Path) -> "SkillRegistry":
        definitions: list[SkillDefinition] = []
        for path in sorted(directory.glob("*.y*ml")):
            with path.open("r", encoding="utf-8") as stream:
                definitions.append(SkillDefinition.model_validate(yaml.safe_load(stream)))
        if not definitions:
            raise ValueError(f"No skill definitions found in {directory}")
        return cls(definitions)

    def get(self, name: str) -> SkillDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise KeyError(f"Unknown skill: {name}") from exc

    def all(self) -> list[SkillDefinition]:
        return list(self._definitions.values())

