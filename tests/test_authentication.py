from __future__ import annotations

import base64
import stat
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from apps.api.main import create_app
from core.bootstrap import Container, build_container
from core.security.auth_key import AuthKeyError, LocalAuthKey
from core.security.authentication import (
    AuthenticationService,
    InvalidCredentialsError,
    InvalidSessionError,
)
from core.services.task_queue import TaskWorker
from core.storage.auth_repository import AuthRepository
from core.storage.database import Database
from core.storage.models import (
    AuthEventRecord,
    AuthSessionRecord,
    UserRecord,
    utc_now,
)

PASSWORD = "test-strong-password-123"


def _install_fast_auth(
    container: Container,
    tmp_path,
    *,
    reauthentication_seconds: int = 300,
    failure_limit: int = 5,
) -> AuthenticationService:
    service = AuthenticationService(
        repository=AuthRepository(container.database),
        auth_key=LocalAuthKey(tmp_path / "persona-auth.key"),
        idle_seconds=30 * 60,
        absolute_seconds=12 * 60 * 60,
        reauthentication_seconds=reauthentication_seconds,
        max_sessions=5,
        failure_limit=failure_limit,
        lockout_seconds=60,
        password_hasher=PasswordHasher(
            time_cost=1,
            memory_cost=8 * 1024,
            parallelism=1,
            hash_len=32,
            salt_len=16,
        ),
    )
    container.authentication = service
    return service


def _create_account(
    service: AuthenticationService,
    *,
    username: str,
    role: str,
) -> dict[str, object]:
    return service.create_account(
        username=username,
        display_name=username.title(),
        password=PASSWORD,
        role=role,
    )


async def _login(
    client: httpx.AsyncClient,
    *,
    username: str,
    password: str = PASSWORD,
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    client.headers["X-CSRF-Token"] = payload["csrf_token"]
    return payload


def test_passwords_sessions_and_auth_key_are_not_stored_as_bearer_secrets(
    container: Container,
    tmp_path,
) -> None:
    service = _install_fast_auth(container, tmp_path)

    assert service.setup_required() is True
    with pytest.raises(ValueError, match="first account must be an admin"):
        _create_account(service, username="first-member", role="member")
    account = _create_account(service, username="first-admin", role="admin")
    grant = service.login(
        username="first-admin",
        password=PASSWORD,
        current_raw_token=None,
        request_id="auth-storage-test",
        user_agent="pytest secret probe",
    )

    with container.database.session(system=True) as session:
        stored_account = session.get(UserRecord, account["id"])
        stored_session = session.scalar(
            select(AuthSessionRecord).where(
                AuthSessionRecord.user_id == account["id"]
            )
        )
        assert stored_account is not None
        assert stored_account.password_hash is not None
        assert stored_account.password_hash.startswith("$argon2id$")
        assert PASSWORD not in stored_account.password_hash
        assert stored_session is not None
        assert stored_session.token_hash == service.hash_session_token(
            grant.raw_token
        )
        assert stored_session.token_hash != grant.raw_token
        assert grant.raw_token not in str(stored_session.__dict__)
        assert "pytest secret probe" not in str(stored_session.__dict__)

    key_path = tmp_path / "persona-auth.key"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    exposed_key = tmp_path / "exposed.key"
    exposed_key.write_bytes(b"K" * 32)
    exposed_key.chmod(0o644)
    with pytest.raises(AuthKeyError, match="group/world accessible"):
        LocalAuthKey(exposed_key).get()


@pytest.mark.asyncio
async def test_api_requires_cookie_csrf_origin_and_server_derived_owner(
    container: Container,
    tmp_path,
) -> None:
    service = _install_fast_auth(container, tmp_path)
    account = _create_account(service, username="alice-admin", role="admin")
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        unauthenticated = await client.get("/api/v1/personas")
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["code"] == "authentication_required"
        assert unauthenticated.headers["cache-control"] == "no-store"

        cross_origin_login = await client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://attacker.example"},
            json={"username": "alice-admin", "password": PASSWORD},
        )
        assert cross_origin_login.status_code == 403

        login = await client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://test"},
            json={"username": "alice-admin", "password": PASSWORD},
        )
        assert login.status_code == 200
        csrf_token = login.json()["csrf_token"]
        first_raw_token = client.cookies.get("personaos_session")
        assert first_raw_token
        set_cookie = login.headers["set-cookie"].lower()
        assert "httponly" in set_cookie
        assert "samesite=strict" in set_cookie
        assert "path=/" in set_cookie

        replacement = await client.post(
            "/api/v1/auth/login",
            json={"username": "alice-admin", "password": PASSWORD},
        )
        assert replacement.status_code == 200
        csrf_token = replacement.json()["csrf_token"]
        raw_token = client.cookies.get("personaos_session")
        assert raw_token != first_raw_token
        with pytest.raises(InvalidSessionError):
            service.authenticate(first_raw_token)

        missing_csrf = await client.post(
            "/api/v1/personas",
            json={"display_name": "Must fail"},
        )
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["code"] == "csrf_validation_failed"

        client.headers["X-CSRF-Token"] = csrf_token
        wrong_origin = await client.post(
            "/api/v1/personas",
            headers={"Origin": "https://attacker.example"},
            json={"display_name": "Must also fail"},
        )
        assert wrong_origin.status_code == 403
        assert wrong_origin.json()["code"] == "origin_validation_failed"

        forged_owner = await client.post(
            "/api/v1/personas",
            json={
                "display_name": "Forged owner",
                "owner_id": "victim-account",
            },
        )
        assert forged_owner.status_code == 422

        created = await client.post(
            "/api/v1/personas",
            json={"display_name": "Alice persona"},
        )
        assert created.status_code == 201
        assert created.json()["owner_id"] == account["id"]
        assert service.authenticate(raw_token) is not None

        logout = await client.post("/api/v1/auth/logout")
        assert logout.status_code == 204
        assert not client.cookies.get("personaos_session")

    with pytest.raises(InvalidSessionError):
        service.authenticate(raw_token)


@pytest.mark.asyncio
async def test_two_accounts_cannot_read_or_mutate_each_others_domains(
    container: Container,
    tmp_path,
) -> None:
    service = _install_fast_auth(container, tmp_path)
    alice = _create_account(service, username="alice-admin", role="admin")
    bob = _create_account(service, username="bob-member", role="member")
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)

    async with (
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as alice_client,
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as bob_client,
    ):
        await _login(alice_client, username="alice-admin")
        created_persona = await alice_client.post(
            "/api/v1/personas",
            json={"display_name": "Alice private persona"},
        )
        assert created_persona.status_code == 201
        persona_id = created_persona.json()["id"]

        task = await alice_client.post(
            "/api/v1/tasks/project-maintenance",
            headers={"Idempotency-Key": "alice-private-task"},
            json={"repository": "example/project"},
        )
        assert task.status_code == 202
        task_id = task.json()["task"]["id"]
        forged_task_actor = await alice_client.post(
            "/api/v1/tasks/project-maintenance",
            headers={"Idempotency-Key": "forged-task-actor"},
            json={
                "repository": "example/project",
                "user_id": bob["id"],
            },
        )
        assert forged_task_actor.status_code == 422
        forged_cancellation_actor = await alice_client.post(
            f"/api/v1/tasks/{task_id}/cancel",
            json={
                "reason": "must reject a client actor",
                "requested_by": bob["id"],
            },
        )
        assert forged_cancellation_actor.status_code == 422

        await _login(bob_client, username="bob-member")
        assert (await bob_client.get("/api/v1/personas")).json() == []
        assert (
            await bob_client.get(f"/api/v1/personas/{persona_id}")
        ).status_code == 404
        assert (await bob_client.get("/api/v1/tasks")).json() == []
        assert (
            await bob_client.get(f"/api/v1/tasks/{task_id}")
        ).status_code == 404
        assert (
            await bob_client.post(
                f"/api/v1/tasks/{task_id}/cancel",
                json={"reason": "Bob must not cancel Alice's task"},
            )
        ).status_code == 404
        assert (
            await bob_client.get(
                f"/api/v1/users/{alice['id']}/preferences"
            )
        ).status_code == 404
        own_preferences = await bob_client.get(
            f"/api/v1/users/{bob['id']}/preferences"
        )
        assert own_preferences.status_code == 200
        assert own_preferences.json() == []


@pytest.mark.asyncio
async def test_recent_reauthentication_rotates_cookie_and_csrf(
    container: Container,
    tmp_path,
) -> None:
    service = _install_fast_auth(
        container,
        tmp_path,
        reauthentication_seconds=30,
    )
    admin = _create_account(service, username="security-admin", role="admin")
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        login = await _login(client, username="security-admin")
        old_csrf = str(login["csrf_token"])
        old_token = client.cookies.get("personaos_session")
        session_id = str(login["session"]["id"])
        with container.database.session(system=True) as session:
            auth_session = session.get(AuthSessionRecord, session_id)
            assert auth_session is not None
            auth_session.reauthenticated_at = utc_now() - timedelta(minutes=2)

        denied = await client.post(
            "/api/v1/accounts",
            json={
                "username": "new-member",
                "display_name": "New Member",
                "password": "another-strong-password-123",
                "role": "member",
            },
        )
        assert denied.status_code == 428
        assert denied.json()["code"] == "reauthentication_required"

        reauthenticated = await client.post(
            "/api/v1/auth/reauthenticate",
            json={"password": PASSWORD},
        )
        assert reauthenticated.status_code == 200
        new_csrf = reauthenticated.json()["csrf_token"]
        new_token = client.cookies.get("personaos_session")
        assert new_token != old_token
        assert new_csrf != old_csrf
        with pytest.raises(InvalidSessionError):
            service.authenticate(old_token)

        client.headers["X-CSRF-Token"] = old_csrf
        stale_csrf = await client.post(
            "/api/v1/accounts",
            json={
                "username": "new-member",
                "display_name": "New Member",
                "password": "another-strong-password-123",
                "role": "member",
            },
        )
        assert stale_csrf.status_code == 403

        client.headers["X-CSRF-Token"] = new_csrf
        created = await client.post(
            "/api/v1/accounts",
            json={
                "username": "new-member",
                "display_name": "New Member",
                "password": "another-strong-password-123",
                "role": "member",
            },
        )
        assert created.status_code == 201
        assert created.json()["role"] == "member"

    with container.database.session(system=True) as session:
        events = list(
            session.scalars(
                select(AuthEventRecord).where(
                    AuthEventRecord.account_id == admin["id"]
                )
            )
        )
        assert any(
            item.action == "authorization.reauthentication_required"
            and item.outcome == "denied"
            for item in events
        )
        assert any(
            item.action == "session.reauthenticated"
            and item.detail["token_rotated"] is True
            for item in events
        )


def test_login_lockout_and_expiration_are_audited_without_credentials(
    container: Container,
    tmp_path,
) -> None:
    service = _install_fast_auth(
        container,
        tmp_path,
        failure_limit=2,
    )
    account = _create_account(service, username="lockout-admin", role="admin")

    for _ in range(2):
        with pytest.raises(InvalidCredentialsError):
            service.login(
                username="lockout-admin",
                password="definitely-wrong-password",
                current_raw_token=None,
                request_id="lockout-test",
                user_agent=None,
            )
    with pytest.raises(InvalidCredentialsError):
        service.login(
            username="lockout-admin",
            password=PASSWORD,
            current_raw_token=None,
            request_id="lockout-test",
            user_agent=None,
        )

    with container.database.session(system=True) as session:
        stored_account = session.get(UserRecord, account["id"])
        assert stored_account is not None
        assert stored_account.locked_until is not None
        stored_account.locked_until = utc_now() - timedelta(seconds=1)

    grant = service.login(
        username="lockout-admin",
        password=PASSWORD,
        current_raw_token=None,
        request_id="expiry-test",
        user_agent=None,
    )
    for _ in range(2):
        with pytest.raises(InvalidCredentialsError):
            service.reauthenticate(
                principal=grant.principal,
                password="definitely-wrong-password",
                request_id="reauth-lockout-test",
            )
    with pytest.raises(InvalidCredentialsError):
        service.reauthenticate(
            principal=grant.principal,
            password=PASSWORD,
            request_id="reauth-lockout-test",
        )
    with container.database.session(system=True) as session:
        stored_account = session.get(UserRecord, account["id"])
        assert stored_account is not None
        assert stored_account.locked_until is not None
        stored_account.locked_until = utc_now() - timedelta(seconds=1)

    grant = service.reauthenticate(
        principal=grant.principal,
        password=PASSWORD,
        request_id="reauth-lockout-recovered",
    )
    with container.database.session(system=True) as session:
        auth_session = session.get(
            AuthSessionRecord,
            grant.principal.session_id,
        )
        assert auth_session is not None
        auth_session.idle_expires_at = utc_now() - timedelta(seconds=1)

    with pytest.raises(InvalidSessionError):
        service.authenticate(grant.raw_token)

    with container.database.session(system=True) as session:
        auth_session = session.get(
            AuthSessionRecord,
            grant.principal.session_id,
        )
        assert auth_session is not None
        assert auth_session.revoke_reason == "expired"
        events = list(
            session.scalars(
                select(AuthEventRecord).where(
                    AuthEventRecord.request_id == "lockout-test"
                )
            )
        )
        serialized = repr(
            [
                {
                    "action": item.action,
                    "subject_hash": item.subject_hash,
                    "detail": item.detail,
                }
                for item in events
            ]
        )
        assert "definitely-wrong-password" not in serialized
        assert "lockout-admin" not in serialized
        assert all(
            item.subject_hash is None or len(item.subject_hash) == 64
            for item in events
        )


@pytest.mark.asyncio
async def test_two_accounts_complete_evidence_loops_without_cross_leakage(
    container: Container,
    tmp_path,
) -> None:
    isolated = build_container(
        settings=replace(
            container.settings,
            persona_blob_dir=tmp_path / "blobs",
            persona_blob_key=base64.urlsafe_b64encode(b"A" * 32).decode(),
            persona_blob_key_path=None,
        ),
        database=Database("sqlite://"),
    )
    service = _install_fast_auth(isolated, tmp_path)
    _create_account(service, username="alice-admin", role="admin")
    _create_account(service, username="bob-member", role="member")
    app = create_app(isolated)
    transport = httpx.ASGITransport(app=app)

    async with (
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as alice_client,
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as bob_client,
    ):
        await _login(alice_client, username="alice-admin")
        await _login(bob_client, username="bob-member")
        clients = {
            "alice": alice_client,
            "bob": bob_client,
        }
        loops: dict[str, dict[str, str]] = {}
        source = (
            "2025-03-04，我加入 PersonaOS 项目，负责可追溯记忆系统设计。"
        ).encode()
        for label, client in clients.items():
            persona_response = await client.post(
                "/api/v1/personas",
                json={"display_name": f"{label.title()} Persona"},
            )
            assert persona_response.status_code == 201
            persona_id = persona_response.json()["id"]
            upload_response = await client.post(
                f"/api/v1/personas/{persona_id}/documents",
                files={"file": ("career.md", source, "text/markdown")},
            )
            assert upload_response.status_code == 202
            upload = upload_response.json()
            loops[label] = {
                "persona_id": persona_id,
                "document_id": upload["document"]["id"],
                "task_id": upload["queue_submission"]["task_id"],
            }

        worker = TaskWorker(
            store=isolated.store,
            project_maintenance=isolated.project_maintenance,
            task_handlers=isolated.task_handlers,
            worker_id="isolation-worker",
            retry_delay_seconds=0,
        )
        assert (await worker.run_one())["status"] == "completed"
        assert (await worker.run_one())["status"] == "completed"

        for label, client in clients.items():
            loop = loops[label]
            candidates = (
                await client.get(
                    f"/api/v1/personas/{loop['persona_id']}/memory-candidates"
                )
            ).json()
            assert len(candidates) == 1
            memory_id = candidates[0]["memory"]["id"]
            confirmed = await client.post(
                f"/api/v1/memory-candidates/{memory_id}/review",
                json={"action": "confirm", "reason": "source verified"},
            )
            assert confirmed.status_code == 200
            conversation = await client.post(
                f"/api/v1/personas/{loop['persona_id']}/conversations",
                json={"title": f"{label} evidence"},
            )
            conversation_id = conversation.json()["id"]
            answer = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={
                    "content": "我什么时候加入 PersonaOS？",
                    "top_k": 3,
                },
            )
            assert answer.status_code == 201
            answer_payload = answer.json()
            assert answer_payload["assistant_message"]["answer_status"] == (
                "answered"
            )
            assert answer_payload["citations"][0]["source"]["id"] == (
                loop["document_id"]
            )
            exported = await client.post(
                f"/api/v1/personas/{loop['persona_id']}/export",
                json={"include_raw_sources": True},
            )
            assert exported.status_code == 200
            assert exported.json()["manifest"]["included_raw_sources"] is True
            loop.update(
                {
                    "memory_id": memory_id,
                    "conversation_id": conversation_id,
                    "message_id": answer_payload["assistant_message"]["id"],
                }
            )

        assert loops["alice"]["task_id"] != loops["bob"]["task_id"]
        for label, client in clients.items():
            other = loops["bob" if label == "alice" else "alice"]
            assert {
                item["id"] for item in (await client.get("/api/v1/personas")).json()
            } == {loops[label]["persona_id"]}
            for path in (
                f"/api/v1/personas/{other['persona_id']}",
                f"/api/v1/documents/{other['document_id']}",
                f"/api/v1/memories/{other['memory_id']}",
                f"/api/v1/tasks/{other['task_id']}",
                (
                    f"/api/v1/conversations/"
                    f"{other['conversation_id']}/messages"
                ),
                f"/api/v1/messages/{other['message_id']}/citations",
                f"/api/v1/personas/{other['persona_id']}/audit-events",
            ):
                assert (await client.get(path)).status_code == 404
            assert (
                await client.post(
                    f"/api/v1/personas/{other['persona_id']}/export",
                    json={"include_raw_sources": True},
                )
            ).status_code == 404
            assert (
                await client.delete(
                    f"/api/v1/documents/{other['document_id']}?confirm=true"
                )
            ).status_code == 404
