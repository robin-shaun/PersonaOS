from __future__ import annotations

import base64
import json
import stat
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import func, select

from apps.api.main import create_app
from core.bootstrap import Container, build_container
from core.ingestion.chunking import DeterministicTextChunker
from core.ingestion.extractor import RulesMemoryCandidateExtractor
from core.security.access import AccessContext
from core.services.task_queue import TaskWorker
from core.storage.blob import BlobStoreError, EncryptedLocalBlobStore
from core.storage.database import Database
from core.storage.models import (
    AuditEventRecord,
    DocumentChunkRecord,
    PersonaMemoryEvidenceRecord,
    PersonaMemoryRecord,
    PersonaMemoryVersionRecord,
    QueueJobRecord,
    TaskRecord,
    utc_now,
)
from core.storage.persona_repository import PersonaRepository


@pytest.fixture
def persona_container(
    container: Container,
    tmp_path,
) -> Container:
    key = base64.urlsafe_b64encode(b"K" * 32).decode("ascii")
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


def test_encrypted_blob_store_is_content_addressed_and_authenticated(
    tmp_path,
) -> None:
    content = "私密原始资料：只允许授权人物空间读取。".encode()
    store = EncryptedLocalBlobStore(
        root=tmp_path / "blobs",
        key=b"A" * 32,
    )

    first = store.put(content)
    replay = store.put(content)

    assert first.created is True
    assert replay.created is False
    assert replay.object_key == first.object_key
    encrypted_path = store.root / first.object_key
    encrypted = encrypted_path.read_bytes()
    assert encrypted.startswith(b"POSB1")
    assert content not in encrypted
    assert (
        store.get(
            first.object_key,
            expected_sha256=first.content_sha256,
        )
        == content
    )

    wrong_key = EncryptedLocalBlobStore(
        root=store.root,
        key=b"B" * 32,
    )
    with pytest.raises(BlobStoreError, match="failed authentication"):
        wrong_key.get(
            first.object_key,
            expected_sha256=first.content_sha256,
        )
    with pytest.raises(ValueError, match="invalid content-addressed"):
        store.get("../secret", expected_sha256=first.content_sha256)

    generated_key_store = EncryptedLocalBlobStore(
        root=tmp_path / "generated" / "blobs",
    )
    generated_key_store.put(b"generate a protected local key")
    generated_key_path = tmp_path / "generated" / "persona_blob.key"
    assert stat.S_IMODE(generated_key_path.stat().st_mode) == 0o600

    insecure_key_path = tmp_path / "insecure.key"
    insecure_key_path.write_bytes(b"C" * 32)
    insecure_key_path.chmod(0o644)
    insecure_key_store = EncryptedLocalBlobStore(
        root=tmp_path / "insecure-blobs",
        key_path=insecure_key_path,
    )
    with pytest.raises(BlobStoreError, match="group/world accessible"):
        insecure_key_store.put(b"must reject an exposed key file")


def test_chunker_and_rules_extractor_are_stable_and_source_bound() -> None:
    text = (
        "2025-03-04 我加入了开源项目。\n\n"
        + "项目背景与可验证资料。" * 100
        + "\n\n我的偏好是先给结论，再说明依据。"
    )
    chunker = DeterministicTextChunker(max_chars=400, overlap_chars=40)

    first = chunker.split(text)
    replay = chunker.split(text)

    assert first == replay
    assert len(first) >= 2
    for index, chunk in enumerate(first):
        assert chunk.ordinal == index
        assert chunk.content == text[chunk.char_start : chunk.char_end]
        assert chunk.locator["char_start"] == chunk.char_start
        assert chunk.locator["line_start"] == chunk.line_start

    extractor = RulesMemoryCandidateExtractor()
    candidates = extractor.extract(first)
    assert candidates == extractor.extract(replay)
    assert all(item.epistemic_status == "source_verified" for item in candidates)
    assert any(item.memory_type == "episodic" for item in candidates)
    assert any(item.memory_type == "preference" for item in candidates)


def test_new_source_cannot_mutate_confirmed_memory_evidence(
    persona_container: Container,
) -> None:
    access = AccessContext(owner_id="local-user", actor_id="local-user")
    repository = PersonaRepository(persona_container.database)
    persona = repository.create_persona(
        access,
        display_name="证据隔离测试人物",
        description="",
    )
    content = "我偏好所有长期记忆先审核再确认。" * 12
    chunks = DeterministicTextChunker(max_chars=400).split(content)
    candidates = RulesMemoryCandidateExtractor().extract(chunks)

    first_document = repository.upsert_document(
        access,
        persona_id=persona["id"],
        original_filename="first.md",
        media_type="text/markdown",
        object_key=f"sha256/aa/{'a' * 64}.blob",
        content_sha256="a" * 64,
        byte_size=len(content.encode()),
        language="zh-CN",
    )["document"]
    repository.persist_ingestion(
        access=access,
        document_id=first_document["id"],
        chunks=chunks,
        candidates=candidates,
    )
    first_candidate = repository.list_memory_bundles(
        access,
        persona_id=persona["id"],
        status="candidate",
    )[0]
    first_confirmed = repository.review_memory(
        access,
        first_candidate["memory"]["id"],
        action="confirm",
        edited_content=None,
        reason="第一份资料已人工确认",
    )
    assert len(first_confirmed["evidence"]) == 1

    second_document = repository.upsert_document(
        access,
        persona_id=persona["id"],
        original_filename="second.md",
        media_type="text/markdown",
        object_key=f"sha256/bb/{'b' * 64}.blob",
        content_sha256="b" * 64,
        byte_size=len(content.encode()),
        language="zh-CN",
    )["document"]
    repository.persist_ingestion(
        access=access,
        document_id=second_document["id"],
        chunks=chunks,
        candidates=candidates,
    )

    unchanged = repository.get_memory_bundle(
        access,
        first_candidate["memory"]["id"],
    )
    assert len(unchanged["versions"]) == 2
    assert len(unchanged["evidence"]) == 1
    new_candidates = repository.list_memory_bundles(
        access,
        persona_id=persona["id"],
        status="candidate",
    )
    assert len(new_candidates) == 1
    assert new_candidates[0]["memory"]["id"] != unchanged["memory"]["id"]
    assert (
        new_candidates[0]["evidence"][0]["source_document"]["id"]
        == (second_document["id"])
    )


@pytest.mark.asyncio
async def test_ingestion_failure_does_not_leak_raw_source(
    persona_container: Container,
    monkeypatch,
) -> None:
    access = persona_container.persona_access
    persona = persona_container.personas.create(
        access,
        display_name="失败隔离测试人物",
    )
    private_phrase = "绝不能进入任务错误记录的私密正文"
    upload = persona_container.personas.upload_text(
        access,
        persona_id=persona["id"],
        filename="private.md",
        media_type="text/markdown",
        content=private_phrase.encode(),
    )
    task_id = upload["queue_submission"]["task_id"]
    document_id = upload["document"]["id"]

    def fail_with_sensitive_database_message(**_) -> dict:
        raise RuntimeError(f"database parameters contained: {private_phrase}")

    monkeypatch.setattr(
        persona_container.knowledge_ingestion._personas,
        "persist_ingestion",
        fail_with_sensitive_database_message,
    )
    worker = TaskWorker(
        store=persona_container.store,
        project_maintenance=persona_container.project_maintenance,
        task_handlers=persona_container.task_handlers,
        worker_id="failure-redaction-worker",
        retry_delay_seconds=0,
    )
    result = await worker.run_one()

    assert result is not None
    assert result["status"] == "retry_scheduled"
    trace = persona_container.store.get_task_bundle(task_id)
    document = persona_container.personas.get_document(access, document_id)
    audits = persona_container.personas.list_audit_events(
        access,
        persona_id=persona["id"],
    )
    persisted_trace = json.dumps(
        {
            "worker_result": result,
            "task_trace": trace,
            "document": document,
            "audits": audits,
        },
        ensure_ascii=False,
    )
    assert private_phrase not in persisted_trace
    assert "KnowledgeIngestionPersistenceError" in persisted_trace
    assert document["document"]["status"] == "failed"
    assert document["chunks"] == []


@pytest.mark.asyncio
async def test_persona_evidence_loop_is_idempotent_and_auditable(
    persona_container: Container,
    authenticate_client,
) -> None:
    first_section = (
        "2025-03-04，我加入 PersonaOS 项目，并负责整理可追溯的技术决策。"
        + "这段经历来自当天的项目日记。" * 90
    )
    private_phrase = "我喜欢手冲咖啡，写方案时习惯先给结论，再列证据。"
    second_section = private_phrase + "这是明确写入资料的个人偏好。" * 90
    source_text = f"{first_section}\n\n{second_section}\n"
    source_bytes = source_text.encode("utf-8")

    app = create_app(persona_container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        await authenticate_client(client, persona_container)
        created = await client.post(
            "/api/v1/personas",
            headers={"X-Request-ID": "persona-create-1"},
            json={
                "display_name": "Shaun 的数字分身",
                "description": "仅依据用户审核过的资料回答。",
            },
        )
        assert created.status_code == 201
        persona = created.json()
        persona_id = persona["id"]
        assert persona["owner_id"] == "local-user"
        assert "不是现实中的本人" in persona["simulation_notice"]

        uploaded = await client.post(
            f"/api/v1/personas/{persona_id}/documents",
            headers={"X-Request-ID": "document-upload-1"},
            files={
                "file": (
                    "journal.md",
                    source_bytes,
                    "text/markdown",
                )
            },
        )
        assert uploaded.status_code == 202
        upload = uploaded.json()
        document_id = upload["document"]["id"]
        task_id = upload["queue_submission"]["task_id"]
        assert upload["document"]["status"] == "uploaded"
        assert "object_key" not in upload["document"]
        assert upload["document_created"] is True
        assert upload["blob_created"] is True

        replayed = await client.post(
            f"/api/v1/personas/{persona_id}/documents",
            files={
                "file": (
                    "renamed-copy.md",
                    source_bytes,
                    "text/markdown",
                )
            },
        )
        assert replayed.status_code == 202
        replay = replayed.json()
        assert replay["document"]["id"] == document_id
        assert replay["queue_submission"]["task_id"] == task_id
        assert replay["document_created"] is False
        assert replay["blob_created"] is False
        assert replay["queue_submission"]["idempotency_replayed"] is True

        encrypted_files = list(
            persona_container.settings.persona_blob_dir.rglob("*.blob")
        )
        assert len(encrypted_files) == 1
        assert source_bytes not in encrypted_files[0].read_bytes()

        worker = TaskWorker(
            store=persona_container.store,
            project_maintenance=persona_container.project_maintenance,
            task_handlers=persona_container.task_handlers,
            worker_id="persona-worker",
            lease_seconds=30,
            retry_delay_seconds=0,
        )
        first_run = await worker.run_one()
        assert first_run is not None
        assert first_run["status"] == "completed"
        assert first_run["task_status"] == "completed"

        document_response = await client.get(f"/api/v1/documents/{document_id}")
        assert document_response.status_code == 200
        document_bundle = document_response.json()
        assert document_bundle["document"]["status"] == "ready"
        assert document_bundle["document"]["created_at"].endswith("+00:00")
        assert len(document_bundle["chunks"]) >= 2
        for chunk in document_bundle["chunks"]:
            assert (
                chunk["content"] == source_text[chunk["char_start"] : chunk["char_end"]]
            )

        candidates_response = await client.get(
            f"/api/v1/personas/{persona_id}/memory-candidates"
        )
        assert candidates_response.status_code == 200
        candidates = candidates_response.json()
        assert len(candidates) == len(document_bundle["chunks"])
        for candidate in candidates:
            assert candidate["memory"]["status"] == "candidate"
            assert candidate["memory"]["epistemic_status"] == "source_verified"
            assert (
                candidate["current_version"]["metadata_snapshot"]["source_bound"]
                is True
            )
            assert (
                candidate["current_version"]["metadata_snapshot"]["is_model_inference"]
                is False
            )
            assert candidate["evidence"]
            evidence = candidate["evidence"][0]
            assert evidence["source_document"]["id"] == document_id
            assert "object_key" not in evidence["source_document"]
            assert evidence["document_chunk"]["locator"]["kind"] == "text_range"

        trace = persona_container.store.get_task_bundle(task_id)
        serialized_trace = json.dumps(trace, ensure_ascii=False)
        assert private_phrase not in serialized_trace
        assert source_text not in serialized_trace
        assert (
            trace["workflow_runs"][0]["state"]["extract_candidates"][
                "model_inferences_created"
            ]
            == 0
        )

        with persona_container.database.session() as session:
            task = session.get(TaskRecord, task_id)
            queue_job = session.scalar(
                select(QueueJobRecord).where(QueueJobRecord.task_id == task_id)
            )
            assert task is not None
            assert queue_job is not None
            task.status = "pending"
            task.final_output = None
            queue_job.status = "queued"
            queue_job.available_at = utc_now()
            queue_job.finished_at = None
            queue_job.lease_owner = None
            queue_job.lease_expires_at = None

        retry_run = await worker.run_one()
        assert retry_run is not None
        assert retry_run["status"] == "completed"

        with persona_container.database.session() as session:
            assert session.scalar(select(func.count(DocumentChunkRecord.id))) == len(
                document_bundle["chunks"]
            )
            assert session.scalar(select(func.count(PersonaMemoryRecord.id))) == len(
                candidates
            )
            assert session.scalar(
                select(func.count(PersonaMemoryVersionRecord.id))
            ) == len(candidates)
            assert session.scalar(
                select(func.count(PersonaMemoryEvidenceRecord.id))
            ) == len(candidates)
            assert (
                session.scalar(
                    select(func.count(AuditEventRecord.id)).where(
                        AuditEventRecord.action == "document.processed"
                    )
                )
                == 1
            )

        confirmed_id = candidates[0]["memory"]["id"]
        confirmed = await client.post(
            f"/api/v1/memory-candidates/{confirmed_id}/review",
            headers={"X-Request-ID": "memory-confirm-1"},
            json={
                "action": "confirm",
                "edited_content": "我在 2025-03-04 加入 PersonaOS 项目。",
                "reason": "去掉重复背景，但保留原始证据。",
            },
        )
        assert confirmed.status_code == 200
        confirmed_memory = confirmed.json()
        assert confirmed_memory["memory"]["status"] == "confirmed"
        assert confirmed_memory["current_version"]["version"] == 2
        assert (
            confirmed_memory["current_version"]["metadata_snapshot"]["user_confirmed"]
            is True
        )
        assert (
            confirmed_memory["current_version"]["metadata_snapshot"][
                "content_edited_during_confirmation"
            ]
            is True
        )
        assert [item["version"] for item in confirmed_memory["versions"]] == [
            1,
            2,
        ]
        assert (
            confirmed_memory["versions"][0]["metadata_snapshot"]["user_confirmed"]
            is False
        )
        assert confirmed_memory["evidence"]

        confirmed_replay = await client.post(
            f"/api/v1/memory-candidates/{confirmed_id}/review",
            json={
                "action": "confirm",
                "edited_content": "我在 2025-03-04 加入 PersonaOS 项目。",
                "reason": "HTTP 重试不应新建版本。",
            },
        )
        assert confirmed_replay.status_code == 200
        assert len(confirmed_replay.json()["versions"]) == 2

        conflicting_replay = await client.post(
            f"/api/v1/memory-candidates/{confirmed_id}/review",
            json={
                "action": "confirm",
                "edited_content": "与已确认版本不同的正文。",
            },
        )
        assert conflicting_replay.status_code == 409

        unedited_id = candidates[1]["memory"]["id"]
        unedited = await client.post(
            f"/api/v1/memory-candidates/{unedited_id}/review",
            json={
                "action": "confirm",
                "reason": "原始候选内容准确，直接确认。",
            },
        )
        assert unedited.status_code == 200
        unedited_memory = unedited.json()
        assert unedited_memory["current_version"]["version"] == 2
        assert (
            unedited_memory["current_version"]["raw_content"]
            == (candidates[1]["current_version"]["raw_content"])
        )
        assert (
            unedited_memory["current_version"]["metadata_snapshot"]["user_confirmed"]
            is True
        )
        assert (
            unedited_memory["current_version"]["metadata_snapshot"][
                "content_edited_during_confirmation"
            ]
            is False
        )
        assert unedited_memory["evidence"]

        for candidate in candidates[2:]:
            rejected = await client.post(
                f"/api/v1/memory-candidates/{candidate['memory']['id']}/review",
                json={
                    "action": "reject",
                    "reason": "本轮演示只确认一条记忆。",
                },
            )
            assert rejected.status_code == 200
            assert rejected.json()["memory"]["status"] == "rejected"

        remaining = await client.get(f"/api/v1/personas/{persona_id}/memory-candidates")
        assert remaining.json() == []
        confirmed_list = await client.get(f"/api/v1/personas/{persona_id}/memories")
        assert len(confirmed_list.json()) == 2
        rejected_list = await client.get(
            f"/api/v1/personas/{persona_id}/memories",
            params={"status": "rejected"},
        )
        assert len(rejected_list.json()) == len(candidates) - 2

        audits_response = await client.get(
            f"/api/v1/personas/{persona_id}/audit-events"
        )
        assert audits_response.status_code == 200
        audits = audits_response.json()
        actions = [item["action"] for item in audits]
        assert actions.count("persona.created") == 1
        assert actions.count("document.uploaded") == 1
        assert actions.count("document.processed") == 1
        assert actions.count("memory.confirmed") == 2
        assert actions.count("memory.rejected") == len(candidates) - 2
        audits_by_action = {item["action"]: item for item in audits}
        assert audits_by_action["persona.created"]["request_id"] == ("persona-create-1")
        assert audits_by_action["document.uploaded"]["request_id"] == (
            "document-upload-1"
        )
        assert private_phrase not in json.dumps(audits, ensure_ascii=False)

    with persona_container.database.session() as session:
        assert (
            session.scalar(select(func.count(PersonaMemoryVersionRecord.id)))
            == len(candidates) + 2
        )
        assert (
            session.scalar(select(func.count(PersonaMemoryEvidenceRecord.id)))
            == len(candidates) + 2
        )

    with pytest.raises(KeyError, match="PersonaRecord not found"):
        persona_container.personas.get(
            AccessContext(owner_id="another-user", actor_id="another-user"),
            persona_id,
        )
