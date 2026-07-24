from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PreferenceCandidateInput(BaseModel):
    context: str
    category: str
    rule: str
    extraction_method: str
    weight: float = Field(ge=0.0, le=1.0)


class PersonalEvidence(BaseModel):
    user_id: str
    task_id: str
    source_type: str
    source_id: str
    source_kind: str
    captured_at: str
    content: dict[str, Any] = Field(default_factory=dict)
    candidate: PreferenceCandidateInput | None = None


class AppliedPreference(BaseModel):
    preference_id: str
    context: str
    category: str
    rule: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_count: int = Field(ge=1)


class PersonalContextPolicy(BaseModel):
    evidence_required: bool = True
    confirmed_only: bool = True
    candidate_preferences_applied: bool = False


class PersonalContext(BaseModel):
    """Versionable input boundary shared by every Agent runtime."""

    schema_version: str = "1.0"
    user_id: str
    context: str
    identity_profile: dict[str, Any] | None = None
    memories: list[dict[str, Any]] = Field(default_factory=list)
    preferences: list[AppliedPreference] = Field(default_factory=list)
    policy: PersonalContextPolicy = Field(default_factory=PersonalContextPolicy)
