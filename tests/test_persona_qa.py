from __future__ import annotations

import base64
import json
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import delete, func, select

from apps.api.main import create_app
from core.bootstrap import Container, build_container
from core.evaluation.persona_qa_eval import PersonaQAEvaluator
from core.ingestion.chunking import DeterministicTextChunker
from core.ingestion.extractor import RulesMemoryCandidateExtractor
from core.retrieval.answering import (
    CitationValidationError,
    validate_answer_citations,
)
from core.retrieval.embeddings import FeatureHashEmbeddingProvider
from core.retrieval.models import AnswerClaim, AnswerDraft, RetrievedEvidence
from core.retrieval.repository import PersonaRetrievalRepository
from core.retrieval.service import MemoryIndexService
from core.security.access import AccessContext
from core.services.task_queue import TaskWorker
from core.storage.database import Database
from core.storage.models import (
    AuditEventRecord,
    EmbeddingSpaceRecord,
    PersonaMemoryEmbeddingRecord,
    PersonaMemoryRecord,
)
from core.storage.persona_repository import PersonaRepository


@pytest.fixture
def persona_qa_container(
    container: Container,
    tmp_path,
) -> Container:
    key = base64.urlsafe_b64encode(b"Q" * 32).decode("ascii")
    settings = replace(
        container.settings,
        persona_blob_dir=tmp_path / "blobs",
        persona_blob_key=key,
        persona_blob_key_path=None,
    )
    return build_container(
        settings=settings,
        database=Database("sqlite://"),
    )


@pytest.mark.asyncio
async def test_review_gates_retrieval_and_answer_citations_are_resolvable(
    persona_qa_container: Container,
    monkeypatch,
    authenticate_client,
) -> None:
    private_source = "2025-03-04，我加入 PersonaOS 项目，负责可追溯记忆系统设计。"
    app = create_app(persona_qa_container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        await authenticate_client(client, persona_qa_container)
        created = await client.post(
            "/api/v1/personas",
            json={"display_name": "引用测试人物"},
        )
        persona_id = created.json()["id"]
        uploaded = await client.post(
            f"/api/v1/personas/{persona_id}/documents",
            files={
                "file": (
                    "career.md",
                    private_source.encode(),
                    "text/markdown",
                )
            },
        )
        assert uploaded.status_code == 202
        worker = TaskWorker(
            store=persona_qa_container.store,
            project_maintenance=persona_qa_container.project_maintenance,
            task_handlers=persona_qa_container.task_handlers,
            worker_id="persona-qa-ingestion",
            retry_delay_seconds=0,
        )
        assert (await worker.run_one())["status"] == "completed"
        candidates = (
            await client.get(f"/api/v1/personas/{persona_id}/memory-candidates")
        ).json()
        assert len(candidates) == 1
        memory_id = candidates[0]["memory"]["id"]

        conversation = await client.post(
            f"/api/v1/personas/{persona_id}/conversations",
            json={"title": "有证据问答"},
        )
        conversation_id = conversation.json()["id"]
        before_review = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "我什么时候加入 PersonaOS？", "top_k": 3},
        )
        assert before_review.status_code == 201
        no_memory = before_review.json()
        assert no_memory["assistant_message"]["answer_status"] == "no_memory"
        assert no_memory["retrieval_run"]["candidates"] == []
        assert no_memory["citations"] == []
        assert no_memory["model_call"]["status"] == "skipped"

        confirmed = await client.post(
            f"/api/v1/memory-candidates/{memory_id}/review",
            json={"action": "confirm", "reason": "原始资料可验证"},
        )
        assert confirmed.status_code == 200
        confirmed_payload = confirmed.json()
        assert confirmed_payload["indexing"]["created"] is True
        current_version_id = confirmed_payload["current_version"]["id"]

        def reject_duplicate_embedding(_: list[str]) -> list[list[float]]:
            raise AssertionError("an existing embedding must not be recomputed")

        monkeypatch.setattr(
            persona_qa_container.memory_index._embeddings,
            "embed_documents",
            reject_duplicate_embedding,
        )
        confirmed_replay = await client.post(
            f"/api/v1/memory-candidates/{memory_id}/review",
            json={"action": "confirm", "reason": "HTTP retry"},
        )
        assert confirmed_replay.status_code == 200
        assert confirmed_replay.json()["indexing"]["created"] is False

        answered = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "我什么时候加入 PersonaOS？", "top_k": 3},
        )
        assert answered.status_code == 201
        answer = answered.json()
        assert answer["assistant_message"]["answer_status"] == "answered"
        assert "[C1]" in answer["assistant_message"]["content"]
        assert answer["assistant_message"]["claims"][0]["citation_ids"] == ["C1"]
        assert answer["model_call"]["status"] == "completed"
        assert answer["model_call"]["data_boundary"] == "local"
        assert answer["retrieval_run"]["filters"]["memory_status"] == "confirmed"
        assert answer["retrieval_run"]["filters"]["current_version_only"] is True
        assert len(answer["citations"]) == 1
        citation = answer["citations"][0]
        assert citation["citation"]["memory_id"] == memory_id
        assert citation["citation"]["memory_version_id"] == current_version_id
        assert citation["citation"]["excerpt"] == private_source
        assert citation["memory"]["status"] == "confirmed"
        assert citation["source"]["filename"] == "career.md"
        assert citation["source"]["locator"]["kind"] == "text_range"

        citations = await client.get(
            f"/api/v1/messages/{answer['assistant_message']['id']}/citations"
        )
        assert citations.status_code == 200
        assert citations.json() == answer["citations"]

        unknown = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "我最喜欢什么颜色？", "top_k": 3},
        )
        assert unknown.status_code == 201
        assert unknown.json()["assistant_message"]["answer_status"] == "no_memory"

        audits = (
            await client.get(f"/api/v1/personas/{persona_id}/audit-events")
        ).json()
        actions = [item["action"] for item in audits]
        assert "memory.indexed" in actions
        assert "question.no_memory" in actions
        assert "question.answered" in actions
        assert private_source not in json.dumps(audits, ensure_ascii=False)

    evaluator = PersonaQAEvaluator()
    report = evaluator.evaluate(
        answer,
        allowed_memory_ids={memory_id},
        expected_embedding_space_id=(
            persona_qa_container.memory_index.embedding_space_id
        ),
    )
    assert report.passed is True
    assert report.unauthorized_recall_count == 0
    assert report.wrong_embedding_space_count == 0
    assert report.dangling_citation_count == 0
    no_memory_report = evaluator.evaluate(
        no_memory,
        allowed_memory_ids=set(),
        expected_embedding_space_id=(
            persona_qa_container.memory_index.embedding_space_id
        ),
    )
    assert no_memory_report.passed is True
    assert no_memory_report.no_evidence_boundary_valid is True


def test_owner_and_embedding_spaces_are_hard_retrieval_boundaries(
    persona_qa_container: Container,
) -> None:
    access = persona_qa_container.persona_access
    memory = _seed_confirmed_memory(
        persona_qa_container,
        access=access,
        content="我习惯先给结论，再列出可以验证的证据。",
    )
    repository = PersonaRetrievalRepository(persona_qa_container.database)
    query = "我的写作习惯是什么？"

    with pytest.raises(KeyError, match="PersonaRecord not found"):
        repository.rank_lexical(
            AccessContext(owner_id="another-user", actor_id="another-user"),
            persona_id=memory["persona_id"],
            query=query,
            limit=10,
            minimum_score=0,
        )

    old_space_id = persona_qa_container.memory_index.embedding_space_id
    new_provider = FeatureHashEmbeddingProvider(dimensions=512)
    new_index = MemoryIndexService(
        repository=repository,
        embeddings=new_provider,
    )
    empty_new_space = repository.rank_vector(
        access,
        persona_id=memory["persona_id"],
        embedding_space_id=new_provider.space.id,
        query_embedding=new_provider.embed_query(query),
        limit=10,
        minimum_similarity=-1,
    )
    assert empty_new_space == []

    indexed = new_index.index_memory(access, memory["memory_id"])
    assert indexed["embedding_space_id"] != old_space_id
    new_space_rank = repository.rank_vector(
        access,
        persona_id=memory["persona_id"],
        embedding_space_id=new_provider.space.id,
        query_embedding=new_provider.embed_query(query),
        limit=10,
        minimum_similarity=-1,
    )
    assert [item["memory_version_id"] for item in new_space_rank] == [
        memory["memory_version_id"]
    ]

    with persona_qa_container.database.session() as session:
        spaces = session.scalar(select(func.count(EmbeddingSpaceRecord.id)))
        embedding_records = list(
            session.scalars(
                select(PersonaMemoryEmbeddingRecord).where(
                    PersonaMemoryEmbeddingRecord.memory_id == memory["memory_id"]
                )
            )
        )
    assert spaces == 2
    assert len(embedding_records) == 2
    assert {item.memory_version_id for item in embedding_records} == {
        memory["memory_version_id"]
    }


def test_citation_validator_rejects_model_fabrication() -> None:
    evidence = RetrievedEvidence(
        citation_id="C1",
        rank=1,
        memory_id="memory-1",
        memory_version_id="version-1",
        evidence_id="evidence-1",
        evidence_relation="supports",
        source_document_id="document-1",
        document_chunk_id="chunk-1",
        memory_type="semantic",
        epistemic_status="source_verified",
        sensitivity="private",
        summary="已确认事实",
        excerpt="原始资料",
        locator={"line_start": 1},
        source={"filename": "source.md"},
        lexical_rank=1,
        lexical_score=1,
        rrf_score=0.1,
        embedding_space_id="space-1",
    )
    fabricated = AnswerDraft(
        answer="模型声称存在另一条资料 [C999]",
        claims=[
            AnswerClaim(
                text="模型声称存在另一条资料",
                citation_ids=["C999"],
            )
        ],
    )
    with pytest.raises(CitationValidationError, match="unknown citations"):
        validate_answer_citations(fabricated, [evidence])


@pytest.mark.asyncio
async def test_revectorization_is_audited_idempotent_background_work(
    persona_qa_container: Container,
    authenticate_client,
) -> None:
    access = persona_qa_container.persona_access
    memory = _seed_confirmed_memory(
        persona_qa_container,
        access=access,
        content="我负责维护 PersonaOS 的记忆索引。",
    )
    with persona_qa_container.database.session() as session:
        session.execute(
            delete(PersonaMemoryEmbeddingRecord).where(
                PersonaMemoryEmbeddingRecord.memory_id == memory["memory_id"]
            )
        )

    app = create_app(persona_qa_container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        await authenticate_client(client, persona_qa_container)
        first = await client.post(
            f"/api/v1/personas/{memory['persona_id']}/memories/reindex",
            headers={"Idempotency-Key": "embedding-model-change-1"},
        )
        replay = await client.post(
            f"/api/v1/personas/{memory['persona_id']}/memories/reindex",
            headers={"Idempotency-Key": "embedding-model-change-1"},
        )
        assert first.status_code == 202
        assert replay.status_code == 202
        assert replay.json()["task_id"] == first.json()["task_id"]
        assert replay.json()["idempotency_replayed"] is True

        worker = TaskWorker(
            store=persona_qa_container.store,
            project_maintenance=persona_qa_container.project_maintenance,
            task_handlers=persona_qa_container.task_handlers,
            worker_id="memory-reindex-worker",
            retry_delay_seconds=0,
        )
        result = await worker.run_one()
        assert result["status"] == "completed"
        trace = persona_qa_container.store.get_task_bundle(first.json()["task_id"])
        assert trace["task"]["final_output"]["created_count"] == 1
        assert trace["task"]["final_output"]["indexed_count"] == 1
        assert memory["content"] not in json.dumps(trace, ensure_ascii=False)

    with persona_qa_container.database.session() as session:
        count = session.scalar(
            select(func.count(PersonaMemoryEmbeddingRecord.id)).where(
                PersonaMemoryEmbeddingRecord.memory_id == memory["memory_id"]
            )
        )
        audits = list(
            session.scalars(
                select(AuditEventRecord).where(
                    AuditEventRecord.action == "memories.reindexed"
                )
            )
        )
    assert count == 1
    assert len(audits) == 1
    assert audits[0].detail["task_id"] == first.json()["task_id"]


def _seed_confirmed_memory(
    container: Container,
    *,
    access: AccessContext,
    content: str,
) -> dict[str, str]:
    repository = PersonaRepository(container.database)
    persona = repository.create_persona(
        access,
        display_name="检索边界测试人物",
        description="",
    )
    digest = ("a" if access.owner_id == "local-user" else "b") * 64
    document = repository.upsert_document(
        access,
        persona_id=persona["id"],
        original_filename="memory.md",
        media_type="text/markdown",
        object_key=f"sha256/{digest[:2]}/{digest}.blob",
        content_sha256=digest,
        byte_size=len(content.encode()),
        language="zh-CN",
    )["document"]
    chunks = DeterministicTextChunker().split(content)
    candidates = RulesMemoryCandidateExtractor().extract(chunks)
    repository.persist_ingestion(
        access=access,
        document_id=document["id"],
        chunks=chunks,
        candidates=candidates,
    )
    candidate = repository.list_memory_bundles(
        access,
        persona_id=persona["id"],
        status="candidate",
    )[0]
    confirmed = container.personas.review_memory(
        access,
        candidate["memory"]["id"],
        action="confirm",
        edited_content=None,
        reason="test fixture",
    )
    with container.database.session() as session:
        record = session.get(PersonaMemoryRecord, candidate["memory"]["id"])
        assert record is not None
    return {
        "persona_id": persona["id"],
        "memory_id": candidate["memory"]["id"],
        "memory_version_id": confirmed["current_version"]["id"],
        "content": content,
    }
