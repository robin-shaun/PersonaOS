from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import inspect

from adapters.github.app import GitHubAppClient
from apps.api.main import create_app
from core.bootstrap import Container, build_container
from core.config import Settings
from core.services.task_queue import TaskWorker
from core.storage.database import Database


class ForbiddenFallbackGateway:
    async def get_repository_snapshot(
        self,
        repository: str,
        *,
        max_items: int = 50,
    ) -> Any:
        raise AssertionError(
            f"fallback GitHub gateway used for {repository} ({max_items})"
        )


def generate_rsa_keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_key = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_key, public_key


def test_settings_require_complete_github_app_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)

    with pytest.raises(ValueError, match="must be configured together"):
        Settings.from_env(root)

    monkeypatch.setenv(
        "GITHUB_APP_PRIVATE_KEY",
        "-----BEGIN PRIVATE KEY-----\\nsecret\\n-----END PRIVATE KEY-----",
    )
    settings = Settings.from_env(root)

    assert settings.github_app_configured is True
    assert "\n" in (settings.github_app_private_key or "")
    assert "secret" not in repr(settings)


@pytest.mark.asyncio
async def test_github_app_signs_jwt_and_caches_scoped_installation_token() -> None:
    private_key, public_key = generate_rsa_keys()
    now = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "token": "installation-secret",
                    "expires_at": (now + timedelta(hours=1)).isoformat(),
                    "permissions": {
                        "issues": "read",
                        "pull_requests": "read",
                    },
                    "repository_selection": "selected",
                },
            )
        return httpx.Response(
            200,
            json={
                "full_name": "example/project",
                "owner": {"login": "example"},
                "private": True,
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://api.github.test",
    ) as http_client:
        app_client = GitHubAppClient(
            app_id="12345",
            private_key=private_key,
            api_url="https://api.github.test",
            client=http_client,
            clock=lambda: now,
        )
        app_jwt = app_client.create_app_jwt()
        claims = jwt.decode(
            app_jwt,
            public_key,
            algorithms=["RS256"],
            options={"verify_exp": False, "verify_iat": False},
        )
        assert claims["iss"] == "12345"
        assert claims["iat"] == int((now - timedelta(seconds=60)).timestamp())
        assert claims["exp"] - claims["iat"] == 600

        first = await app_client.verify_repository_access(
            9876,
            "example/project",
        )
        second = await app_client.verify_repository_access(
            9876,
            "EXAMPLE/PROJECT",
        )
        cached_token = await app_client.get_installation_token(
            9876,
            "example/project",
        )

    token_requests = [request for request in requests if request.method == "POST"]
    assert len(token_requests) == 1
    token_request = token_requests[0]
    assert token_request.url.path == "/app/installations/9876/access_tokens"
    assert json.loads(token_request.content) == {
        "repositories": ["project"],
        "permissions": {
            "issues": "read",
            "pull_requests": "read",
        },
    }
    assert token_request.headers["authorization"].startswith("Bearer eyJ")
    assert first == second
    assert first.repository == "example/project"
    assert first.private is True
    assert "installation-secret" not in repr(cached_token)


@pytest.mark.asyncio
async def test_github_connection_drives_worker_without_persisting_secrets() -> None:
    private_key, _ = generate_rsa_keys()
    now = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
    installation_secret = "installation-secret-never-persist"
    requests: list[httpx.Request] = []

    repository_payload = {
        "full_name": "example/project",
        "owner": {"login": "example"},
        "private": True,
        "description": "Private example project",
        "html_url": "https://github.com/example/project",
        "default_branch": "main",
        "stargazers_count": 0,
        "forks_count": 0,
        "subscribers_count": 1,
        "open_issues_count": 1,
    }
    issue_payload = {
        "number": 10,
        "title": "Security boundary is bypassed",
        "body": "Reproduction steps",
        "labels": [{"name": "security"}, {"name": "bug"}],
        "user": {"login": "alice"},
        "comments": 3,
        "reactions": {"total_count": 2},
        "state": "open",
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-07-20T00:00:00Z",
        "html_url": "https://github.com/example/project/issues/10",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(
                201,
                json={
                    "token": installation_secret,
                    "expires_at": (now + timedelta(hours=1)).isoformat(),
                    "permissions": {
                        "issues": "read",
                        "pull_requests": "read",
                    },
                    "repository_selection": "selected",
                },
            )
        assert request.headers["authorization"] == f"Bearer {installation_secret}"
        if request.url.path == "/repos/example/project":
            return httpx.Response(200, json=repository_payload)
        if request.url.path == "/repos/example/project/issues":
            return httpx.Response(200, json=[issue_payload])
        if request.url.path == "/repos/example/project/pulls":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "not found"})

    root = Path(__file__).resolve().parents[1]
    settings = Settings(
        base_dir=root,
        database_url="sqlite://",
        employee_config_dir=root / "data" / "employee_templates",
        skill_config_dir=root / "data" / "skills",
        workflow_config_dir=root / "data" / "workflows",
        github_token="legacy-secret-never-persist",
        github_api_url="https://api.github.test",
        runtime_name="rules",
        github_app_id="12345",
        github_app_private_key=private_key,
    )
    assert private_key not in repr(settings)
    assert "legacy-secret-never-persist" not in repr(settings)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://api.github.test",
    ) as http_client:
        github_app = GitHubAppClient(
            app_id="12345",
            private_key=private_key,
            api_url="https://api.github.test",
            client=http_client,
            clock=lambda: now,
        )
        container = build_container(
            settings=settings,
            database=Database("sqlite://"),
            github=ForbiddenFallbackGateway(),
            github_app=github_app,
        )
        app = create_app(container)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            health = await client.get("/health")
            assert health.json()["github_auth"] == "github_app"

            connected = await client.post(
                "/api/v1/github/connections",
                json={
                    "user_id": "shaun",
                    "installation_id": 9876,
                    "repository": "example/project",
                },
            )
            assert connected.status_code == 201
            connection = connected.json()
            assert connection["status"] == "active"
            assert connection["private"] is True
            assert connection["permissions"] == {
                "issues": "read",
                "pull_requests": "read",
            }
            assert "token" not in connection

            not_owned = await client.post(
                "/api/v1/tasks/project-maintenance",
                json={
                    "github_connection_id": connection["id"],
                    "user_id": "other-user",
                },
            )
            assert not_owned.status_code == 422

            submitted = await client.post(
                "/api/v1/tasks/project-maintenance",
                headers={"Idempotency-Key": "private-project-brief"},
                json={
                    "github_connection_id": connection["id"],
                    "user_id": "shaun",
                    "max_items": 20,
                },
            )
            assert submitted.status_code == 202
            task = submitted.json()
            assert task["task"]["input"] == {
                "repository": "example/project",
                "max_items": 20,
                "read_only": True,
                "github_connection_id": connection["id"],
            }

            worker = TaskWorker(
                store=container.store,
                project_maintenance=container.project_maintenance,
                worker_id="github-app-worker",
                lease_seconds=30,
                retry_delay_seconds=0,
            )
            worker_result = await worker.run_one()
            assert worker_result is not None
            assert worker_result["status"] == "completed"

            trace = await client.get(f"/api/v1/tasks/{task['task']['id']}")
            assert trace.json()["task"]["status"] == "awaiting_approval"
            assert trace.json()["tool_calls"][0]["status"] == "completed"

            wrong_user_disconnect = await client.delete(
                f"/api/v1/github/connections/{connection['id']}",
                params={"user_id": "other-user"},
            )
            assert wrong_user_disconnect.status_code == 404

            disconnected = await client.delete(
                f"/api/v1/github/connections/{connection['id']}",
                params={"user_id": "shaun"},
            )
            assert disconnected.status_code == 200
            assert disconnected.json()["status"] == "disconnected"

            active_connections = await client.get(
                "/api/v1/github/connections",
                params={"user_id": "shaun"},
            )
            assert active_connections.json() == []
            all_connections = await client.get(
                "/api/v1/github/connections",
                params={
                    "user_id": "shaun",
                    "include_disconnected": True,
                },
            )
            assert all_connections.json()[0]["status"] == "disconnected"

            cannot_reuse = await client.post(
                "/api/v1/tasks/project-maintenance",
                json={
                    "github_connection_id": connection["id"],
                    "user_id": "shaun",
                },
            )
            assert cannot_reuse.status_code == 422

    persisted_values: list[str] = []
    with container.database.engine.connect() as connection:
        for table_name in inspect(container.database.engine).get_table_names():
            rows = connection.exec_driver_sql(
                f'SELECT * FROM "{table_name}"'
            ).fetchall()
            persisted_values.append(repr(rows))
    persisted_text = "\n".join(persisted_values)
    assert installation_secret not in persisted_text
    assert private_key not in persisted_text
    assert "legacy-secret-never-persist" not in persisted_text
    assert sum(request.method == "POST" for request in requests) == 1


@pytest.mark.asyncio
async def test_github_connection_requires_app_configuration(
    container: Container,
) -> None:
    app = create_app(container)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/github/connections",
            json={
                "user_id": "shaun",
                "installation_id": 9876,
                "repository": "example/project",
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"] == ("GitHub App authentication is not configured")
