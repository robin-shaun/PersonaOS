from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillDefinition(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    inputs: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    evaluation: list[str] = Field(default_factory=list)

