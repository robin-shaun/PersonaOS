from __future__ import annotations

from time import perf_counter
from typing import Any

from core.retrieval.answering import (
    NO_EVIDENCE_ANSWER,
    validate_answer_citations,
)
from core.retrieval.models import PersonaAnswerGenerator, RetrievedEvidence
from core.retrieval.repository import (
    PersonaRetrievalRepository,
    canonical_hash,
)
from core.retrieval.service import HybridRetrievalService, MemoryIndexService
from core.security.access import AccessContext
from core.security.data_policy import (
    allowed_sensitivities_for_boundary,
    require_boundary_allowed,
)


class PersonaQuestionAnsweringService:
    def __init__(
        self,
        *,
        repository: PersonaRetrievalRepository,
        index: MemoryIndexService,
        retrieval: HybridRetrievalService,
        generator: PersonaAnswerGenerator,
    ) -> None:
        self._repository = repository
        self._index = index
        self._retrieval = retrieval
        self._generator = generator

    def create_conversation(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        title: str | None,
    ) -> dict[str, Any]:
        normalized_title = " ".join((title or "").split()) or None
        if normalized_title is not None and len(normalized_title) > 300:
            raise ValueError("conversation title must not exceed 300 characters")
        return self._repository.create_conversation(
            access,
            persona_id=persona_id,
            title=normalized_title,
        )

    def list_messages(
        self,
        access: AccessContext,
        *,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        return self._repository.list_messages(
            access,
            conversation_id=conversation_id,
        )

    async def ask(
        self,
        access: AccessContext,
        *,
        conversation_id: str,
        question: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        normalized = " ".join(question.split())
        if not normalized:
            raise ValueError("question must not be empty")
        if len(normalized) > 10_000:
            raise ValueError("question must not exceed 10000 characters")
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")

        context = self._repository.get_conversation_context(
            access,
            conversation_id=conversation_id,
        )
        model_data_boundary = self._generator.data_boundary
        require_boundary_allowed(
            allowed_boundaries=context["allowed_model_boundaries"],
            requested_boundary=model_data_boundary,
        )
        query_embedding_data_boundary = self._retrieval.data_boundary
        require_boundary_allowed(
            allowed_boundaries=context["allowed_model_boundaries"],
            requested_boundary=query_embedding_data_boundary,
        )
        allowed_sensitivities = allowed_sensitivities_for_boundary(
            model_data_boundary
        )
        model_evidence_projection = (
            "full_evidence" if model_data_boundary == "local" else "memory_only"
        )
        user_message = self._repository.create_user_message(
            access,
            conversation_id=conversation_id,
            content=normalized,
        )
        persona_id = str(user_message["persona_id"])
        indexing = self._index.ensure_persona_indexed(
            access,
            persona_id=persona_id,
        )
        evidence = self._retrieval.search(
            access,
            persona_id=persona_id,
            query=normalized,
            top_k=top_k,
            allowed_sensitivities=allowed_sensitivities,
        )
        trace = [
            {
                "citation_id": item.citation_id,
                "rank": item.rank,
                "memory_id": item.memory_id,
                "memory_version_id": item.memory_version_id,
                "lexical_rank": item.lexical_rank,
                "lexical_score": item.lexical_score,
                "vector_rank": item.vector_rank,
                "vector_similarity": item.vector_similarity,
                "rrf_score": item.rrf_score,
                "embedding_space_id": item.embedding_space_id,
            }
            for item in evidence
        ]
        query_hash = canonical_hash({"question": normalized})
        retrieval_run = self._repository.create_retrieval_run(
            access,
            conversation_id=conversation_id,
            user_message_id=str(user_message["id"]),
            embedding_space_id=self._retrieval.embedding_space_id,
            query_sha256=query_hash,
            candidates=trace,
            top_k=top_k,
            model_data_boundary=model_data_boundary,
            query_embedding_data_boundary=query_embedding_data_boundary,
            allowed_sensitivities=allowed_sensitivities,
            model_evidence_projection=model_evidence_projection,
        )
        request_hash = canonical_hash(
            {
                "query_sha256": query_hash,
                "embedding_space_id": self._retrieval.embedding_space_id,
                "memory_version_ids": [item.memory_version_id for item in evidence],
                "citation_ids": [item.citation_id for item in evidence],
                "prompt_template_version": "persona-answer-v1",
                "model_data_boundary": model_data_boundary,
                "query_embedding_data_boundary": (
                    query_embedding_data_boundary
                ),
                "allowed_sensitivities": allowed_sensitivities,
                "model_evidence_projection": model_evidence_projection,
            }
        )

        if not evidence:
            persisted = self._repository.persist_no_evidence_answer(
                access,
                conversation_id=conversation_id,
                retrieval_run_id=str(retrieval_run["id"]),
                answer=NO_EVIDENCE_ANSWER,
                request_hash=request_hash,
            )
        else:
            started = perf_counter()
            generation = await self._generator.generate(
                question=normalized,
                evidence=self._evidence_for_model(
                    evidence,
                    model_data_boundary=model_data_boundary,
                ),
            )
            if generation.data_boundary != model_data_boundary:
                raise ValueError(
                    "Answer generator data boundary changed during generation"
                )
            latency_ms = int((perf_counter() - started) * 1000)
            claim_indexes = validate_answer_citations(
                generation.draft,
                evidence,
            )
            persisted = self._repository.persist_answer(
                access,
                conversation_id=conversation_id,
                retrieval_run_id=str(retrieval_run["id"]),
                generation=generation,
                evidence=evidence,
                claim_indexes=claim_indexes,
                request_hash=request_hash,
                latency_ms=latency_ms,
            )

        return {
            "user_message": user_message,
            "assistant_message": persisted["assistant_message"],
            "retrieval_run": retrieval_run,
            "citations": persisted["citations"],
            "model_call": persisted["model_call"],
            "indexing": indexing,
        }

    def get_citations(
        self,
        access: AccessContext,
        *,
        message_id: str,
    ) -> list[dict[str, Any]]:
        return self._repository.get_message_citations(
            access,
            message_id=message_id,
        )

    @staticmethod
    def _evidence_for_model(
        evidence: list[RetrievedEvidence],
        *,
        model_data_boundary: str,
    ) -> list[RetrievedEvidence]:
        if model_data_boundary == "local":
            return evidence
        return [
            item.model_copy(
                update={
                    "excerpt": "",
                    "locator": {},
                    "source": {
                        "id": item.source_document_id,
                        "content_withheld": True,
                    },
                }
            )
            for item in evidence
        ]
