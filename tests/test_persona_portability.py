from __future__ import annotations

import base64
import json
from dataclasses import replace

import httpx
import pytest

from apps.api.main import create_app
from core.bootstrap import Container, build_container
from core.services.task_queue import TaskWorker
from core.storage.database import Database


def _persistent_container(
    template: Container,
    tmp_path,
    *,
    name: str,
    key_byte: bytes,
) -> Container:
    database_url = f"sqlite:///{tmp_path / f'{name}.db'}"
    settings = replace(
        template.settings,
        database_url=database_url,
        persona_blob_dir=tmp_path / f"{name}-blobs",
        persona_blob_key=base64.urlsafe_b64encode(key_byte * 32).decode("ascii"),
        persona_blob_key_path=None,
        persona_auth_key_path=tmp_path / f"{name}-auth.key",
    )
    return build_container(
        settings=settings,
        database=Database(database_url),
    )


async def _seed_confirmed_memory(
    client: httpx.AsyncClient,
    container: Container,
) -> tuple[str, str]:
    persona = await client.post(
        "/api/v1/personas",
        json={
            "display_name": "可迁移的星航",
            "description": "跨数据库仍保持同一个身份。",
        },
    )
    assert persona.status_code == 201
    persona_id = persona.json()["id"]
    source = "2026-08-02，我决定把每周日留给家人，并把这件事写入长期记忆。"
    uploaded = await client.post(
        f"/api/v1/personas/{persona_id}/documents",
        files={"file": ("journal.md", source.encode(), "text/markdown")},
    )
    assert uploaded.status_code == 202
    worker = TaskWorker(
        store=container.store,
        project_maintenance=container.project_maintenance,
        task_handlers=container.task_handlers,
        worker_id="portability-worker",
        lease_seconds=30,
        retry_delay_seconds=0,
    )
    result = await worker.run_one()
    assert result is not None and result["status"] == "completed"
    candidates = (
        await client.get(f"/api/v1/personas/{persona_id}/memory-candidates")
    ).json()
    assert candidates
    reviewed = await client.post(
        f"/api/v1/memory-candidates/{candidates[0]['memory']['id']}/review",
        json={"action": "confirm", "reason": "用户确认这是重要经历。"},
    )
    assert reviewed.status_code == 200
    return persona_id, source


@pytest.mark.asyncio
async def test_export_import_preserves_identity_memory_and_restart(
    container: Container,
    authenticate_client,
    tmp_path,
) -> None:
    source_container = _persistent_container(
        container,
        tmp_path,
        name="source",
        key_byte=b"S",
    )
    source_app = create_app(source_container)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=source_app),
        base_url="http://source",
    ) as source_client:
        await authenticate_client(source_client, source_container)
        persona_id, source_text = await _seed_confirmed_memory(
            source_client,
            source_container,
        )
        exported_response = await source_client.post(
            f"/api/v1/personas/{persona_id}/export",
            json={"include_raw_sources": True},
        )
        assert exported_response.status_code == 200
        package = exported_response.json()
        assert package["export"]["persona"]["id"] == persona_id
        assert package["export"]["raw_sources"][0]["content_base64"]

    target_container = _persistent_container(
        container,
        tmp_path,
        name="target",
        key_byte=b"T",
    )
    target_app = create_app(target_container)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=target_app),
        base_url="http://target",
    ) as target_client:
        await authenticate_client(target_client, target_container)
        imported_response = await target_client.post(
            "/api/v1/personas/import",
            json=package,
        )
        assert imported_response.status_code == 201, imported_response.text
        imported = imported_response.json()
        assert imported["persona"]["id"] == persona_id
        assert imported["persona"]["allowed_model_boundaries"] == ["local"]
        assert imported["manifest"]["identity_preserved"] is True
        assert imported["restored"]["memory_count"] >= 1
        assert imported["indexing"]["eligible_count"] >= 1

        duplicate = await target_client.post(
            "/api/v1/personas/import",
            json=package,
        )
        assert duplicate.status_code == 409

    restarted = _persistent_container(
        container,
        tmp_path,
        name="target",
        key_byte=b"T",
    )
    restarted_app = create_app(restarted)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted_app),
        base_url="http://restarted",
    ) as restarted_client:
        await authenticate_client(restarted_client, restarted)
        personas = (await restarted_client.get("/api/v1/personas")).json()
        assert [item["id"] for item in personas] == [persona_id]
        memories = (
            await restarted_client.get(
                f"/api/v1/personas/{persona_id}/memories?status=confirmed"
            )
        ).json()
        assert memories
        assert "每周日" in memories[0]["current_version"]["raw_content"]
        assert memories[0]["evidence"][0]["source_document"]["created_at"]

        other = await restarted_client.post(
            "/api/v1/personas",
            json={"display_name": "空白人物"},
        )
        other_id = other.json()["id"]
        assert (
            await restarted_client.get(
                f"/api/v1/personas/{other_id}/memories?status=confirmed"
            )
        ).json() == []

        conversation = await restarted_client.post(
            f"/api/v1/personas/{persona_id}/conversations",
            json={"title": "重启后的回忆"},
        )
        answer = await restarted_client.post(
            f"/api/v1/conversations/{conversation.json()['id']}/messages",
            json={"content": "我把每周什么时候留给家人？", "top_k": 5},
        )
        assert answer.status_code == 201
        assert "每周日" in answer.json()["assistant_message"]["content"]
        assert source_text in package["export"]["raw_sources"][0]["content"]


@pytest.mark.asyncio
async def test_import_rejects_tampered_export_without_partial_state(
    container: Container,
    authenticate_client,
    tmp_path,
) -> None:
    source = _persistent_container(
        container,
        tmp_path,
        name="tamper-source",
        key_byte=b"A",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(source)),
        base_url="http://source",
    ) as client:
        await authenticate_client(client, source)
        persona_id, _ = await _seed_confirmed_memory(client, source)
        package = (
            await client.post(
                f"/api/v1/personas/{persona_id}/export",
                json={"include_raw_sources": True},
            )
        ).json()

    tampered = json.loads(json.dumps(package))
    tampered["export"]["persona"]["display_name"] = "被篡改的身份"
    target = _persistent_container(
        container,
        tmp_path,
        name="tamper-target",
        key_byte=b"B",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(target)),
        base_url="http://target",
    ) as client:
        await authenticate_client(client, target)
        response = await client.post("/api/v1/personas/import", json=tampered)
        assert response.status_code == 422
        assert "SHA-256" in response.json()["detail"]
        assert (await client.get("/api/v1/personas")).json() == []
        assert not list((tmp_path / "tamper-target-blobs").rglob("*.blob"))
