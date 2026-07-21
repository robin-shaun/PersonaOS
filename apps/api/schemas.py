from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectMaintenanceTaskCreate(BaseModel):
    repository: str = Field(
        min_length=3,
        max_length=200,
        examples=["owner/repository"],
    )
    employee_id: str = Field(
        default="github-maintainer-001",
        min_length=1,
        max_length=100,
    )
    user_id: str = Field(default="local-user", min_length=1, max_length=64)
    workflow_name: str = Field(
        default="daily-project-maintenance",
        min_length=1,
        max_length=120,
    )
    max_items: int = Field(default=50, ge=1, le=100)

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        normalized = value.strip().strip("/")
        if len(normalized.split("/")) != 2:
            raise ValueError("repository must use the owner/name format")
        return normalized


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "approved_with_edits", "rejected"]
    edited_output: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_edit(self) -> "ApprovalDecisionRequest":
        if self.decision == "approved_with_edits" and self.edited_output is None:
            raise ValueError("edited_output is required for approved_with_edits")
        return self


class FeedbackCreate(BaseModel):
    comment: str = Field(min_length=1, max_length=4000)
    rating: int | None = Field(default=None, ge=1, le=5)

