from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PersonaQAEvaluationReport(BaseModel):
    passed: bool
    unauthorized_recall_count: int
    wrong_embedding_space_count: int
    dangling_citation_count: int
    no_evidence_boundary_valid: bool


class PersonaQAEvaluator:
    """Deterministic safety checks for a persisted persona answer bundle."""

    def evaluate(
        self,
        bundle: dict[str, Any],
        *,
        allowed_memory_ids: set[str],
        expected_embedding_space_id: str,
    ) -> PersonaQAEvaluationReport:
        candidates = list(bundle["retrieval_run"]["candidates"])
        citations = list(bundle["citations"])
        candidate_ids = {
            str(item["citation_id"]): str(item["memory_id"]) for item in candidates
        }
        unauthorized = sum(
            str(item["memory_id"]) not in allowed_memory_ids for item in candidates
        )
        wrong_space = sum(
            str(item["embedding_space_id"]) != expected_embedding_space_id
            for item in candidates
        )
        dangling = 0
        for item in citations:
            citation = item["citation"]
            citation_id = str(citation["citation_id"])
            if (
                candidate_ids.get(citation_id) != str(citation["memory_id"])
                or item["memory"]["status"] != "confirmed"
            ):
                dangling += 1
        no_evidence = bundle["assistant_message"]["answer_status"] == "no_memory"
        no_evidence_boundary_valid = (
            (
                not candidates
                and not citations
                and bundle["model_call"]["status"] == "skipped"
            )
            if no_evidence
            else (
                bool(candidates)
                and bool(citations)
                and bundle["model_call"]["status"] == "completed"
            )
        )
        passed = (
            unauthorized == 0
            and wrong_space == 0
            and dangling == 0
            and no_evidence_boundary_valid
        )
        return PersonaQAEvaluationReport(
            passed=passed,
            unauthorized_recall_count=unauthorized,
            wrong_embedding_space_count=wrong_space,
            dangling_citation_count=dangling,
            no_evidence_boundary_valid=no_evidence_boundary_valid,
        )
