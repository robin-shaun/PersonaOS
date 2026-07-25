from __future__ import annotations

from typing import Any

from core.retrieval.embeddings import EmbeddingProvider
from core.retrieval.models import RetrievedEvidence
from core.retrieval.repository import PersonaRetrievalRepository
from core.security.access import AccessContext


class MemoryIndexService:
    def __init__(
        self,
        *,
        repository: PersonaRetrievalRepository,
        embeddings: EmbeddingProvider,
    ) -> None:
        self._repository = repository
        self._embeddings = embeddings
        self._repository.ensure_embedding_space(self._embeddings.space)

    @property
    def embedding_space_id(self) -> str:
        return self._embeddings.space.id

    def index_memory(
        self,
        access: AccessContext,
        memory_id: str,
    ) -> dict[str, Any]:
        indexable = self._repository.get_indexable_memory(access, memory_id)
        existing = self._repository.existing_memory_embedding(
            access,
            indexable=indexable,
            embedding_space_id=self.embedding_space_id,
        )
        if existing is not None:
            self._repository.record_memory_index_audit(
                access,
                persona_id=str(indexable["persona_id"]),
                memory_id=str(indexable["memory_id"]),
                memory_version_id=str(indexable["memory_version_id"]),
                embedding_space_id=self.embedding_space_id,
                created=False,
            )
            return {
                "embedding_space_id": self.embedding_space_id,
                "memory_id": indexable["memory_id"],
                "memory_version_id": indexable["memory_version_id"],
                "created": False,
            }
        vector = self._embeddings.embed_documents([indexable["content"]])[0]
        result = self._repository.upsert_memory_embedding(
            access,
            indexable=indexable,
            space=self._embeddings.space,
            embedding=vector,
        )
        self._repository.record_memory_index_audit(
            access,
            persona_id=str(indexable["persona_id"]),
            memory_id=str(indexable["memory_id"]),
            memory_version_id=str(indexable["memory_version_id"]),
            embedding_space_id=self.embedding_space_id,
            created=bool(result["created"]),
        )
        return {
            "embedding_space_id": self.embedding_space_id,
            "memory_id": indexable["memory_id"],
            "memory_version_id": indexable["memory_version_id"],
            "created": result["created"],
        }

    def reindex_persona(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        output = self.ensure_persona_indexed(
            access,
            persona_id=persona_id,
        )
        self._repository.record_reindex_audit(
            access,
            persona_id=persona_id,
            embedding_space_id=self.embedding_space_id,
            indexed_count=int(output["indexed_count"]),
            created_count=int(output["created_count"]),
            task_id=task_id,
        )
        return output

    def ensure_persona_indexed(
        self,
        access: AccessContext,
        *,
        persona_id: str,
    ) -> dict[str, Any]:
        eligible = self._repository.list_indexable_memories(
            access,
            persona_id=persona_id,
        )
        indexable = self._repository.list_missing_indexable_memories(
            access,
            persona_id=persona_id,
            embedding_space_id=self.embedding_space_id,
        )
        vectors = (
            self._embeddings.embed_documents(
                [str(item["content"]) for item in indexable]
            )
            if indexable
            else []
        )
        created_count = 0
        for item, vector in zip(indexable, vectors, strict=True):
            result = self._repository.upsert_memory_embedding(
                access,
                indexable=item,
                space=self._embeddings.space,
                embedding=vector,
            )
            created_count += int(result["created"])
        return {
            "persona_id": persona_id,
            "embedding_space_id": self.embedding_space_id,
            "eligible_count": len(eligible),
            "indexed_count": len(indexable),
            "created_count": created_count,
            "unchanged_count": len(eligible) - created_count,
        }

    def validate_persona_access(
        self,
        access: AccessContext,
        *,
        persona_id: str,
    ) -> None:
        self._repository.list_indexable_memories(
            access,
            persona_id=persona_id,
        )


class HybridRetrievalService:
    def __init__(
        self,
        *,
        repository: PersonaRetrievalRepository,
        embeddings: EmbeddingProvider,
        lexical_minimum_score: float = 0.08,
        vector_minimum_similarity: float = 0.16,
        rrf_constant: int = 60,
    ) -> None:
        self._repository = repository
        self._embeddings = embeddings
        self._lexical_minimum_score = lexical_minimum_score
        self._vector_minimum_similarity = vector_minimum_similarity
        self._rrf_constant = max(1, rrf_constant)

    @property
    def embedding_space_id(self) -> str:
        return self._embeddings.space.id

    def search(
        self,
        access: AccessContext,
        *,
        persona_id: str,
        query: str,
        top_k: int,
    ) -> list[RetrievedEvidence]:
        candidate_limit = max(top_k * 5, 20)
        lexical = self._repository.rank_lexical(
            access,
            persona_id=persona_id,
            query=query,
            limit=candidate_limit,
            minimum_score=self._lexical_minimum_score,
        )
        vector = self._repository.rank_vector(
            access,
            persona_id=persona_id,
            embedding_space_id=self.embedding_space_id,
            query_embedding=self._embeddings.embed_query(query),
            limit=candidate_limit,
            minimum_similarity=self._vector_minimum_similarity,
        )
        lexical_by_id = {
            str(item["memory_version_id"]): {
                "rank": rank,
                "score": float(item["score"]),
            }
            for rank, item in enumerate(lexical, start=1)
        }
        vector_by_id = {
            str(item["memory_version_id"]): {
                "rank": rank,
                "similarity": float(item["similarity"]),
            }
            for rank, item in enumerate(vector, start=1)
        }
        version_ids = set(lexical_by_id) | set(vector_by_id)
        ranked: list[tuple[str, float, int]] = []
        for version_id in version_ids:
            lexical_rank = lexical_by_id.get(version_id, {}).get("rank")
            vector_rank = vector_by_id.get(version_id, {}).get("rank")
            score = 0.0
            if lexical_rank is not None:
                score += 1.0 / (self._rrf_constant + int(lexical_rank))
            if vector_rank is not None:
                score += 1.0 / (self._rrf_constant + int(vector_rank))
            best_rank = min(
                int(item) for item in (lexical_rank, vector_rank) if item is not None
            )
            ranked.append((version_id, score, best_rank))
        ranked.sort(key=lambda item: (-item[1], item[2], item[0]))
        selected = ranked[:top_k]
        evidence = self._repository.evidence_for_versions(
            access,
            persona_id=persona_id,
            version_ids=[item[0] for item in selected],
        )
        results: list[RetrievedEvidence] = []
        for version_id, rrf_score, _ in selected:
            item = evidence.get(version_id)
            if item is None:
                continue
            lexical_trace = lexical_by_id.get(version_id)
            vector_trace = vector_by_id.get(version_id)
            results.append(
                RetrievedEvidence(
                    citation_id=f"C{len(results) + 1}",
                    rank=len(results) + 1,
                    **item,
                    lexical_rank=(
                        int(lexical_trace["rank"]) if lexical_trace else None
                    ),
                    lexical_score=(
                        float(lexical_trace["score"]) if lexical_trace else None
                    ),
                    vector_rank=(int(vector_trace["rank"]) if vector_trace else None),
                    vector_similarity=(
                        float(vector_trace["similarity"]) if vector_trace else None
                    ),
                    rrf_score=rrf_score,
                    embedding_space_id=self.embedding_space_id,
                )
            )
        return results
