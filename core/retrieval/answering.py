from __future__ import annotations

from typing import Literal

from core.retrieval.models import (
    AnswerClaim,
    AnswerDraft,
    GenerationResult,
    RetrievedEvidence,
)

NO_EVIDENCE_ANSWER = "没有找到相关的已确认记忆，无法基于现有资料回答。"


class CitationValidationError(ValueError):
    pass


class EvidenceOnlyAnswerGenerator:
    """Offline answer baseline that only restates retrieved confirmed memories."""

    @property
    def data_boundary(self) -> Literal["local"]:
        return "local"

    async def generate(
        self,
        *,
        question: str,
        evidence: list[RetrievedEvidence],
    ) -> GenerationResult:
        del question
        if not evidence:
            raise ValueError("answer generation requires retrieved evidence")
        claims = [
            AnswerClaim(
                text=item.summary,
                citation_ids=[item.citation_id],
            )
            for item in evidence
        ]
        answer = "；".join(
            f"{claim.text} [{claim.citation_ids[0]}]" for claim in claims
        )
        return GenerationResult(
            draft=AnswerDraft(
                answer=f"根据已确认记忆：{answer}",
                claims=claims,
                uncertainty={
                    "level": "evidence_bound",
                    "message": "回答仅复述当前检索到的已确认记忆，可能不完整。",
                },
            ),
            provider="personaos",
            model_name="evidence-only",
            model_version="1.0.0",
            prompt_template_version="persona-answer-v1",
            data_boundary="local",
        )


def validate_answer_citations(
    draft: AnswerDraft,
    evidence: list[RetrievedEvidence],
) -> dict[str, list[int]]:
    available = {item.citation_id for item in evidence}
    if not available:
        raise CitationValidationError("an evidence-backed answer requires citations")
    used: dict[str, list[int]] = {}
    for claim_index, claim in enumerate(draft.claims):
        unique_ids = list(dict.fromkeys(claim.citation_ids))
        if not unique_ids:
            raise CitationValidationError(
                f"claim {claim_index} does not cite any retrieved evidence"
            )
        unknown = set(unique_ids) - available
        if unknown:
            raise CitationValidationError(
                f"claim {claim_index} references unknown citations: "
                f"{', '.join(sorted(unknown))}"
            )
        for citation_id in unique_ids:
            marker = f"[{citation_id}]"
            if marker not in draft.answer:
                raise CitationValidationError(
                    f"answer text is missing citation marker {marker}"
                )
            used.setdefault(citation_id, []).append(claim_index)
    if not draft.claims:
        raise CitationValidationError("answer contains no evidence-backed claims")
    return used
