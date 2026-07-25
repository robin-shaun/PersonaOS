from __future__ import annotations

import base64
import json
from dataclasses import replace
from typing import Literal

import httpx
import pytest
from sqlalchemy import func, select

from apps.api.main import create_app
from core.bootstrap import Container, build_container
from core.retrieval.answering import EvidenceOnlyAnswerGenerator
from core.retrieval.embeddings import EmbeddingSpaceDefinition
from core.retrieval.models import (
    AnswerClaim,
    AnswerDraft,
    GenerationResult,
    RetrievedEvidence,
)
from core.retrieval.repository import PersonaRetrievalRepository
from core.retrieval.service import MemoryIndexService
from core.services.task_queue import TaskWorker
from core.storage.database import Database
from core.storage.models import (
    AnswerCitationRecord,
    DocumentChunkRecord,
    PersonaMemoryEmbeddingRecord,
    PersonaMemoryEvidenceRecord,
    PersonaMemoryRecord,
    PersonaMemoryRelationRecord,
    PersonaMemoryVersionRecord,
    SourceDocumentRecord,
)
from core.storage.persona_lifecycle_repository import DELETED_ANSWER_NOTICE


@pytest.fixture
def lifecycle_container(
    container: Container,
    tmp_path,
) -> Container:
    key = base64.urlsafe_b64encode(b"L" * 32).decode("ascii")
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


class ExternalCaptureGenerator:
    def __init__(self) -> None:
        self.calls: list[list[RetrievedEvidence]] = []

    @property
    def data_boundary(self) -> Literal["external"]:
        return "external"

    async def generate(
        self,
        *,
        question: str,
        evidence: list[RetrievedEvidence],
    ) -> GenerationResult:
        del question
        self.calls.append(evidence)
        claims = [
            AnswerClaim(
                text=item.summary,
                citation_ids=[item.citation_id],
            )
            for item in evidence
        ]
        return GenerationResult(
            draft=AnswerDraft(
                answer="；".join(
                    f"{claim.text} [{claim.citation_ids[0]}]" for claim in claims
                ),
                claims=claims,
            ),
            provider="test",
            model_name="external-capture",
            model_version="1",
            prompt_template_version="test-v1",
            data_boundary="external",
        )


class ExternalRecordingEmbeddings:
    def __init__(self) -> None:
        self.documents: list[str] = []
        self._space = EmbeddingSpaceDefinition(
            provider="test",
            model_name="recording-external",
            model_version="1",
            dimensions=8,
            data_boundary="external",
        )

    @property
    def space(self) -> EmbeddingSpaceDefinition:
        return self._space

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.documents.extend(texts)
        return [[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_versioned_memory_policy_relations_and_export(
    lifecycle_container: Container,
    authenticate_client,
) -> None:
    first_source = "公开线索 Alpha：我习惯先给结论，再列出证据。"
    second_source = "私密线索 Beta：我每周五整理项目日志。"
    app = create_app(lifecycle_container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        await authenticate_client(client, lifecycle_container)
        persona_id = (
            await client.post(
                "/api/v1/personas",
                json={"display_name": "生命周期测试人物"},
            )
        ).json()["id"]
        first = await _upload_process_confirm(
            client,
            lifecycle_container,
            persona_id=persona_id,
            filename="first.md",
            content=first_source,
        )
        second = await _upload_process_confirm(
            client,
            lifecycle_container,
            persona_id=persona_id,
            filename="second.md",
            content=second_source,
        )

        edited = await client.patch(
            f"/api/v1/memories/{first['memory_id']}",
            json={
                "expected_version": 2,
                "content": "公开线索 Alpha：我的写作流程是先结论、后证据。",
                "sensitivity": "public",
                "reason": "用户补充并澄清写作流程",
            },
        )
        assert edited.status_code == 200
        edited_memory = edited.json()
        assert edited_memory["current_version"]["version"] == 3
        assert edited_memory["memory"]["epistemic_status"] == "user_asserted"
        assert edited_memory["memory"]["sensitivity"] == "public"
        assert (
            edited_memory["current_version"]["metadata_snapshot"]["source_bound"]
            is False
        )
        assert edited_memory["evidence"][0]["evidence"]["relation"] == ("derived_from")
        assert edited_memory["indexing"]["created"] is True
        stale = await client.patch(
            f"/api/v1/memories/{first['memory_id']}",
            json={
                "expected_version": 2,
                "sensitivity": "restricted",
                "reason": "过期客户端不应覆盖新版本",
            },
        )
        assert stale.status_code == 409
        assert "version conflict" in stale.json()["detail"]

        self_relation = await client.post(
            f"/api/v1/personas/{persona_id}/memory-relations",
            json={
                "from_memory_id": first["memory_id"],
                "to_memory_id": first["memory_id"],
                "relation": "supports",
            },
        )
        assert self_relation.status_code == 422
        relation = await client.post(
            f"/api/v1/personas/{persona_id}/memory-relations",
            json={
                "from_memory_id": first["memory_id"],
                "to_memory_id": second["memory_id"],
                "relation": "conflicts",
                "confidence": 0.75,
                "evidence_memory_version_ids": [
                    edited_memory["current_version"]["id"],
                    second["memory_version_id"],
                ],
            },
        )
        assert relation.status_code == 201
        relation_id = relation.json()["relation"]["id"]
        assert relation.json()["created"] is True
        listed = await client.get(f"/api/v1/memories/{first['memory_id']}/relations")
        assert [item["id"] for item in listed.json()] == [relation_id]

        exported = await client.post(
            f"/api/v1/personas/{persona_id}/export",
            headers={"X-Request-ID": "export-lifecycle-1"},
            json={"include_raw_sources": True},
        )
        assert exported.status_code == 200
        package = exported.json()
        assert package["export"]["schema_version"] == "persona-export-v1"
        raw_contents = {item["content"] for item in package["export"]["raw_sources"]}
        assert raw_contents == {first_source, second_source}
        assert all(
            "embedding" not in item
            for item in package["export"]["memory_embedding_metadata"]
        )
        serialized_export = json.dumps(package, ensure_ascii=False)
        assert "object_key" not in serialized_export
        assert package["manifest"]["included_raw_sources"] is True

        generator = ExternalCaptureGenerator()
        lifecycle_container.persona_qa._generator = generator
        conversation_id = (
            await client.post(
                f"/api/v1/personas/{persona_id}/conversations",
                json={"title": "模型边界"},
            )
        ).json()["id"]
        denied = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "Alpha 写作流程是什么？", "top_k": 10},
        )
        assert denied.status_code == 403
        messages = await client.get(f"/api/v1/conversations/{conversation_id}/messages")
        assert messages.json() == []
        missing_ack = await client.patch(
            f"/api/v1/personas/{persona_id}/model-policy",
            json={
                "allowed_model_boundaries": ["local", "external"],
                "external_data_acknowledged": False,
            },
        )
        assert missing_ack.status_code == 422
        enabled = await client.patch(
            f"/api/v1/personas/{persona_id}/model-policy",
            json={
                "allowed_model_boundaries": ["local", "external"],
                "external_data_acknowledged": True,
            },
        )
        assert enabled.status_code == 200
        external_embeddings = ExternalRecordingEmbeddings()
        external_index = MemoryIndexService(
            repository=PersonaRetrievalRepository(lifecycle_container.database),
            embeddings=external_embeddings,
        )
        external_indexing = external_index.ensure_persona_indexed(
            lifecycle_container.persona_access,
            persona_id=persona_id,
        )
        assert external_indexing["eligible_count"] == 1
        assert len(external_embeddings.documents) == 1
        assert "Alpha" in external_embeddings.documents[0]
        assert "Beta" not in external_embeddings.documents[0]
        answered = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "Alpha 写作流程是什么？", "top_k": 10},
        )
        assert answered.status_code == 201
        answer = answered.json()
        assert answer["retrieval_run"]["filters"]["model_data_boundary"] == ("external")
        assert (
            answer["retrieval_run"]["filters"]["query_embedding_data_boundary"]
            == "local"
        )
        assert answer["retrieval_run"]["filters"]["allowed_sensitivities"] == ["public"]
        assert answer["retrieval_run"]["filters"]["model_evidence_projection"] == (
            "memory_only"
        )
        assert generator.calls
        assert {item.sensitivity for item in generator.calls[0]} == {"public"}
        assert {item.memory_id for item in generator.calls[0]} == {first["memory_id"]}
        assert all(item.excerpt == "" for item in generator.calls[0])
        assert all(
            item.source
            == {
                "id": item.source_document_id,
                "content_withheld": True,
            }
            for item in generator.calls[0]
        )
        disabled = await client.patch(
            f"/api/v1/personas/{persona_id}/model-policy",
            json={
                "allowed_model_boundaries": ["local"],
                "external_data_acknowledged": False,
            },
        )
        assert disabled.status_code == 200
        lifecycle_container.persona_qa._generator = EvidenceOnlyAnswerGenerator()
        private_answer = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "Beta 每周什么时候整理项目日志？", "top_k": 10},
        )
        assert private_answer.status_code == 201
        private_assistant_id = private_answer.json()["assistant_message"]["id"]

        relation_deleted = await client.delete(
            f"/api/v1/memory-relations/{relation_id}",
            params={"confirm": "true"},
        )
        assert relation_deleted.status_code == 200
        memory_deleted = await client.delete(
            f"/api/v1/memories/{second['memory_id']}",
            params={"confirm": "true"},
        )
        assert memory_deleted.status_code == 200
        assert memory_deleted.json()["embedding_count"] >= 1
        assert memory_deleted.json()["redacted_answer_count"] >= 1
        assert (
            await client.get(f"/api/v1/memories/{second['memory_id']}")
        ).status_code == 404
        messages_after_memory_delete = (
            await client.get(f"/api/v1/conversations/{conversation_id}/messages")
        ).json()
        private_assistant = next(
            item
            for item in messages_after_memory_delete
            if item["id"] == private_assistant_id
        )
        assert private_assistant["content"] == DELETED_ANSWER_NOTICE
        assert (
            await client.get(f"/api/v1/messages/{private_assistant_id}/citations")
        ).json() == []
        assert list(lifecycle_container.settings.persona_blob_dir.rglob("*.blob"))

        audits = (
            await client.get(f"/api/v1/personas/{persona_id}/audit-events")
        ).json()
        actions = [item["action"] for item in audits]
        assert "memory.updated" in actions
        assert "memory_relation.created" in actions
        assert "persona.model_policy_updated" in actions
        assert "persona.exported" in actions
        assert first_source not in json.dumps(audits, ensure_ascii=False)
        assert second_source not in json.dumps(audits, ensure_ascii=False)

    with lifecycle_container.database.session() as session:
        versions = list(
            session.scalars(
                select(PersonaMemoryVersionRecord)
                .where(PersonaMemoryVersionRecord.memory_id == first["memory_id"])
                .order_by(PersonaMemoryVersionRecord.version)
            )
        )
        embeddings = list(
            session.scalars(
                select(PersonaMemoryEmbeddingRecord).where(
                    PersonaMemoryEmbeddingRecord.memory_id == first["memory_id"]
                )
            )
        )
        assert [item.version for item in versions] == [1, 2, 3]
        assert {item.memory_version_id for item in embeddings} == {
            versions[1].id,
            versions[2].id,
        }
        assert (
            session.scalar(
                select(func.count(PersonaMemoryRelationRecord.id)).where(
                    PersonaMemoryRelationRecord.id == relation_id
                )
            )
            == 0
        )


@pytest.mark.asyncio
async def test_document_deletion_removes_blob_graph_search_and_derived_answer(
    lifecycle_container: Container,
    authenticate_client,
) -> None:
    source = "删除证明 Gamma：我负责设计可追溯的记忆删除流程。"
    app = create_app(lifecycle_container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        await authenticate_client(client, lifecycle_container)
        persona_id = (
            await client.post(
                "/api/v1/personas",
                json={"display_name": "删除证明人物"},
            )
        ).json()["id"]
        seeded = await _upload_process_confirm(
            client,
            lifecycle_container,
            persona_id=persona_id,
            filename="delete-me.md",
            content=source,
        )
        document_id = seeded["document_id"]
        memory_id = seeded["memory_id"]
        conversation_id = (
            await client.post(
                f"/api/v1/personas/{persona_id}/conversations",
                json={},
            )
        ).json()["id"]
        answered = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "Gamma 记忆删除流程是谁负责？", "top_k": 5},
        )
        assert answered.status_code == 201
        answer = answered.json()
        assert answer["citations"]
        assistant_id = answer["assistant_message"]["id"]
        blob_files = list(lifecycle_container.settings.persona_blob_dir.rglob("*.blob"))
        assert len(blob_files) == 1

        missing_confirmation = await client.delete(f"/api/v1/documents/{document_id}")
        assert missing_confirmation.status_code == 400
        deleted = await client.delete(
            f"/api/v1/documents/{document_id}",
            params={"confirm": "true"},
        )
        assert deleted.status_code == 200
        receipt = deleted.json()
        assert receipt["deleted"] is True
        assert receipt["blob_deleted"] is True
        assert receipt["chunk_count"] >= 1
        assert receipt["memory_count"] == 1
        assert receipt["embedding_count"] >= 1
        assert receipt["redacted_answer_count"] == 1
        assert not blob_files[0].exists()
        assert (await client.get(f"/api/v1/documents/{document_id}")).status_code == 404
        assert (await client.get(f"/api/v1/memories/{memory_id}")).status_code == 404
        messages = (
            await client.get(f"/api/v1/conversations/{conversation_id}/messages")
        ).json()
        assistant = next(item for item in messages if item["id"] == assistant_id)
        assert assistant["content"] == DELETED_ANSWER_NOTICE
        assert assistant["claims"] == []
        assert assistant["uncertainty"]["level"] == "source_deleted"
        citations = await client.get(f"/api/v1/messages/{assistant_id}/citations")
        assert citations.json() == []
        replay = await client.delete(
            f"/api/v1/documents/{document_id}",
            params={"confirm": "true"},
        )
        assert replay.status_code == 200
        assert replay.json()["idempotency_replayed"] is True

        audits = (
            await client.get(f"/api/v1/personas/{persona_id}/audit-events")
        ).json()
        assert source not in json.dumps(audits, ensure_ascii=False)
        assert [item["action"] for item in audits].count("document.deleted") == 1

    repository = PersonaRetrievalRepository(lifecycle_container.database)
    access = lifecycle_container.persona_access
    lexical = repository.rank_lexical(
        access,
        persona_id=persona_id,
        query="Gamma 删除流程",
        limit=20,
        minimum_score=0,
    )
    vector = repository.rank_vector(
        access,
        persona_id=persona_id,
        embedding_space_id=lifecycle_container.memory_index.embedding_space_id,
        query_embedding=(
            lifecycle_container.memory_index._embeddings.embed_query("Gamma 删除流程")
        ),
        limit=20,
        minimum_similarity=-1,
    )
    assert lexical == []
    assert vector == []
    with lifecycle_container.database.session() as session:
        assert session.get(SourceDocumentRecord, document_id) is None
        assert session.get(PersonaMemoryRecord, memory_id) is None
        assert (
            session.scalar(
                select(func.count(DocumentChunkRecord.id)).where(
                    DocumentChunkRecord.document_id == document_id
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count(PersonaMemoryEvidenceRecord.id)).where(
                    PersonaMemoryEvidenceRecord.source_document_id == document_id
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count(PersonaMemoryEmbeddingRecord.id)).where(
                    PersonaMemoryEmbeddingRecord.memory_id == memory_id
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count(AnswerCitationRecord.id)).where(
                    AnswerCitationRecord.assistant_message_id == assistant_id
                )
            )
            == 0
        )


@pytest.mark.asyncio
async def test_cancelled_processing_document_can_be_deleted(
    lifecycle_container: Container,
    authenticate_client,
) -> None:
    source = "取消中的资料 Epsilon：删除不能永久卡在 processing 状态。"
    app = create_app(lifecycle_container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        await authenticate_client(client, lifecycle_container)
        persona_id = (
            await client.post(
                "/api/v1/personas",
                json={"display_name": "取消后删除人物"},
            )
        ).json()["id"]
        uploaded = await client.post(
            f"/api/v1/personas/{persona_id}/documents",
            files={"file": ("processing.md", source.encode(), "text/markdown")},
        )
        document_id = uploaded.json()["document"]["id"]
        task_id = uploaded.json()["queue_submission"]["task_id"]
        cancellation = lifecycle_container.store.request_task_cancellation(
            task_id,
            requested_by="test",
            reason="simulate worker cancellation after processing began",
        )
        assert cancellation["status"] == "cancelled"
        with lifecycle_container.database.session() as session:
            document = session.get(SourceDocumentRecord, document_id)
            assert document is not None
            document.status = "processing"

        deleted = await client.delete(
            f"/api/v1/documents/{document_id}",
            params={"confirm": "true"},
        )

        assert deleted.status_code == 200
        assert deleted.json()["blob_absent"] is True
        assert (await client.get(f"/api/v1/documents/{document_id}")).status_code == 404


@pytest.mark.asyncio
async def test_shared_content_blob_is_deleted_only_after_last_document(
    lifecycle_container: Container,
    authenticate_client,
) -> None:
    shared = "共享 Blob Delta：同一原始资料被两个人物空间授权导入。"
    app = create_app(lifecycle_container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        await authenticate_client(client, lifecycle_container)
        first_persona = (
            await client.post(
                "/api/v1/personas",
                json={"display_name": "共享人物一"},
            )
        ).json()["id"]
        second_persona = (
            await client.post(
                "/api/v1/personas",
                json={"display_name": "共享人物二"},
            )
        ).json()["id"]
        first = await client.post(
            f"/api/v1/personas/{first_persona}/documents",
            files={"file": ("shared.md", shared.encode(), "text/markdown")},
        )
        second = await client.post(
            f"/api/v1/personas/{second_persona}/documents",
            files={"file": ("shared.md", shared.encode(), "text/markdown")},
        )
        first_document = first.json()["document"]["id"]
        second_document = second.json()["document"]["id"]
        blob_file = next(lifecycle_container.settings.persona_blob_dir.rglob("*.blob"))

        first_deleted = await client.delete(
            f"/api/v1/documents/{first_document}",
            params={"confirm": "true"},
        )
        assert first_deleted.status_code == 200
        assert first_deleted.json()["blob_shared"] is True
        assert first_deleted.json()["blob_deleted"] is False
        assert first_deleted.json()["ingestion_task_cancellation"]["status"] == (
            "cancelled"
        )
        assert blob_file.exists()
        assert (
            await client.get(f"/api/v1/documents/{second_document}")
        ).status_code == 200

        second_deleted = await client.delete(
            f"/api/v1/documents/{second_document}",
            params={"confirm": "true"},
        )
        assert second_deleted.status_code == 200
        assert second_deleted.json()["blob_shared"] is False
        assert second_deleted.json()["blob_deleted"] is True
        assert second_deleted.json()["ingestion_task_cancellation"]["status"] == (
            "cancelled"
        )
        assert not blob_file.exists()


async def _upload_process_confirm(
    client: httpx.AsyncClient,
    container: Container,
    *,
    persona_id: str,
    filename: str,
    content: str,
) -> dict[str, str]:
    uploaded = await client.post(
        f"/api/v1/personas/{persona_id}/documents",
        files={"file": (filename, content.encode(), "text/markdown")},
    )
    assert uploaded.status_code == 202
    document_id = uploaded.json()["document"]["id"]
    worker = TaskWorker(
        store=container.store,
        project_maintenance=container.project_maintenance,
        task_handlers=container.task_handlers,
        worker_id=f"lifecycle-{document_id}",
        retry_delay_seconds=0,
    )
    processed = await worker.run_one()
    assert processed is not None
    assert processed["status"] == "completed"
    candidates = (
        await client.get(f"/api/v1/personas/{persona_id}/memory-candidates")
    ).json()
    candidate = next(
        item
        for item in candidates
        if item["memory"]["source_document_id"] == document_id
    )
    confirmed = await client.post(
        f"/api/v1/memory-candidates/{candidate['memory']['id']}/review",
        json={"action": "confirm", "reason": "生命周期测试确认"},
    )
    assert confirmed.status_code == 200
    return {
        "document_id": document_id,
        "memory_id": candidate["memory"]["id"],
        "memory_version_id": confirmed.json()["current_version"]["id"],
    }
