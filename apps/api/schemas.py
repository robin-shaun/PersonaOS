from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectMaintenanceTaskCreate(BaseModel):
    repository: str | None = Field(
        default=None,
        min_length=3,
        max_length=200,
        examples=["owner/repository"],
    )
    github_connection_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=36,
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
    def validate_repository(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().strip("/")
        if len(normalized.split("/")) != 2:
            raise ValueError("repository must use the owner/name format")
        return normalized

    @field_validator("user_id")
    @classmethod
    def normalize_user_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_id must not be empty")
        return normalized

    @field_validator("github_connection_id")
    @classmethod
    def normalize_connection_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("github_connection_id must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_repository_source(self) -> ProjectMaintenanceTaskCreate:
        if self.repository is None and self.github_connection_id is None:
            raise ValueError(
                "repository or github_connection_id is required"
            )
        return self


class GitHubConnectionCreate(BaseModel):
    user_id: str = Field(default="local-user", min_length=1, max_length=64)
    installation_id: int = Field(ge=1, le=9_223_372_036_854_775_807)
    repository: str = Field(
        min_length=3,
        max_length=200,
        examples=["owner/private-repository"],
    )

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        normalized = value.strip().strip("/")
        if len(normalized.split("/")) != 2:
            raise ValueError("repository must use the owner/name format")
        return normalized

    @field_validator("user_id")
    @classmethod
    def normalize_user_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_id must not be empty")
        return normalized


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "approved_with_edits", "rejected"]
    edited_output: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_edit(self) -> ApprovalDecisionRequest:
        if self.decision == "approved_with_edits" and self.edited_output is None:
            raise ValueError("edited_output is required for approved_with_edits")
        return self


class FeedbackCreate(BaseModel):
    comment: str = Field(min_length=1, max_length=4000)
    rating: int | None = Field(default=None, ge=1, le=5)


class TaskCancellationRequest(BaseModel):
    reason: str = Field(
        default="cancelled by user",
        min_length=1,
        max_length=2000,
    )
    requested_by: str = Field(default="api", min_length=1, max_length=200)


class PreferenceReviewRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    action: Literal["confirm", "reject", "revoke"]
    reason: str | None = Field(default=None, max_length=4000)
    expires_at: datetime | None = None

    @field_validator("user_id")
    @classmethod
    def normalize_preference_user_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_id must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_expiration(self) -> PreferenceReviewRequest:
        if self.expires_at is not None and self.action != "confirm":
            raise ValueError("expires_at is only valid when confirming a preference")
        return self


class PersonaCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("display_name must not be empty")
        return normalized


class PersonaMemoryReviewRequest(BaseModel):
    action: Literal["confirm", "reject"]
    edited_content: str | None = Field(default=None, max_length=20_000)
    reason: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_review(self) -> PersonaMemoryReviewRequest:
        if self.action == "reject" and self.edited_content is not None:
            raise ValueError("edited_content is only valid when confirming")
        if (
            self.edited_content is not None
            and not self.edited_content.strip()
        ):
            raise ValueError("edited_content must not be empty")
        return self


class PersonaConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=300)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class PersonaQuestionCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("content must not be empty")
        return normalized
