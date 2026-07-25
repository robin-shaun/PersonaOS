from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SkillRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_seconds: float = Field(default=0.0, ge=0.0, le=300.0)
    retry_on: list[str] = Field(default_factory=list)


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = "1.0.0"
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_permissions: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=60.0, gt=0.0, le=3600.0)
    retry_policy: SkillRetryPolicy = Field(default_factory=SkillRetryPolicy)
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    requires_confirmation: bool = False
    steps: list[str] = Field(default_factory=list)
    evaluation: list[str] = Field(default_factory=list)
    tests: list[dict[str, Any]] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_contract(self) -> SkillDefinition:
        if not self.input_schema:
            raise ValueError("input_schema must not be empty")
        if not self.output_schema:
            raise ValueError("output_schema must not be empty")
        if not self.tests:
            raise ValueError("tests must contain at least one test case")
        if not self.examples:
            raise ValueError("examples must contain at least one example")
        return self
