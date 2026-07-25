from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class RetrievedEvidence(BaseModel):
    citation_id: str
    rank: int = Field(ge=1)
    memory_id: str
    memory_version_id: str
    evidence_id: str
    source_document_id: str
    document_chunk_id: str
    memory_type: str
    epistemic_status: str
    summary: str
    excerpt: str
    locator: dict[str, Any]
    source: dict[str, Any]
    lexical_rank: int | None = None
    lexical_score: float | None = None
    vector_rank: int | None = None
    vector_similarity: float | None = None
    rrf_score: float
    embedding_space_id: str


class AnswerClaim(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    citation_ids: list[str] = Field(min_length=1)


class AnswerDraft(BaseModel):
    answer: str = Field(min_length=1, max_length=20_000)
    claims: list[AnswerClaim]
    uncertainty: dict[str, Any] = Field(default_factory=dict)


class GenerationResult(BaseModel):
    draft: AnswerDraft
    provider: str
    model_name: str
    model_version: str
    prompt_template_version: str
    data_boundary: Literal["local", "private_network", "external"]
    usage: dict[str, int | float] = Field(default_factory=dict)


class PersonaAnswerGenerator(Protocol):
    async def generate(
        self,
        *,
        question: str,
        evidence: list[RetrievedEvidence],
    ) -> GenerationResult: ...
