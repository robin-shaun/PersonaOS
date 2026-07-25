from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from argon2 import PasswordHasher

from adapters.github.models import (
    GitHubIssue,
    GitHubPullRequest,
    RepositorySnapshot,
)
from core.bootstrap import Container, build_container
from core.config import Settings
from core.security.auth_key import LocalAuthKey
from core.security.authentication import AuthenticationService
from core.storage.auth_repository import AuthRepository
from core.storage.database import Database
from core.storage.models import UserRecord, utc_now

TEST_ACCOUNT_PASSWORD = "test-only-password-123"


class FakeGitHubGateway:
    def __init__(self, snapshot: RepositorySnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[dict[str, object]] = []

    async def get_repository_snapshot(
        self,
        repository: str,
        *,
        max_items: int = 50,
    ) -> RepositorySnapshot:
        self.calls.append({"repository": repository, "max_items": max_items})
        return self.snapshot


@pytest.fixture
def repository_snapshot() -> RepositorySnapshot:
    now = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
    return RepositorySnapshot(
        repository="example/project",
        description="Example open-source project",
        html_url="https://github.com/example/project",
        default_branch="main",
        stars=120,
        forks=14,
        watchers=9,
        open_issues_reported=4,
        fetched_at=now,
        issues=[
            GitHubIssue(
                number=10,
                title="Security boundary is bypassed",
                labels=["security", "bug"],
                author="alice",
                comments=3,
                reactions=2,
                created_at=now - timedelta(days=40),
                updated_at=now - timedelta(days=1),
                html_url="https://github.com/example/project/issues/10",
            ),
            GitHubIssue(
                number=11,
                title="CLI crashes on empty config",
                labels=["bug"],
                author="bob",
                comments=8,
                reactions=3,
                created_at=now - timedelta(days=15),
                updated_at=now - timedelta(days=2),
                html_url="https://github.com/example/project/issues/11",
            ),
            GitHubIssue(
                number=12,
                title="Improve setup guide",
                labels=["documentation"],
                author="carol",
                comments=0,
                reactions=0,
                created_at=now - timedelta(days=90),
                updated_at=now - timedelta(days=45),
                html_url="https://github.com/example/project/issues/12",
            ),
        ],
        pull_requests=[
            GitHubPullRequest(
                number=20,
                title="Fix configuration validation",
                author="dana",
                draft=False,
                comments=2,
                created_at=now - timedelta(days=4),
                updated_at=now - timedelta(hours=8),
                html_url="https://github.com/example/project/pull/20",
            )
        ],
    )


@pytest.fixture
def container(
    repository_snapshot: RepositorySnapshot,
) -> Container:
    root = Path(__file__).resolve().parents[1]
    settings = Settings(
        base_dir=root,
        database_url="sqlite://",
        employee_config_dir=root / "data" / "employee_templates",
        skill_config_dir=root / "data" / "skills",
        workflow_config_dir=root / "data" / "workflows",
        github_token=None,
        github_api_url="https://api.github.test",
        runtime_name="rules",
    )
    return build_container(
        settings=settings,
        database=Database("sqlite://"),
        github=FakeGitHubGateway(repository_snapshot),
    )


@pytest.fixture
def authenticate_client(tmp_path):
    configured: set[int] = set()
    hasher = PasswordHasher(
        time_cost=1,
        memory_cost=8 * 1024,
        parallelism=1,
        hash_len=32,
        salt_len=16,
    )

    async def authenticate(
        client: httpx.AsyncClient,
        container: Container,
        *,
        account_id: str = "local-user",
        username: str | None = None,
        role: str = "admin",
        password: str = TEST_ACCOUNT_PASSWORD,
    ) -> dict[str, object]:
        marker = id(container)
        if marker not in configured:
            container.authentication = AuthenticationService(
                repository=AuthRepository(container.database),
                auth_key=LocalAuthKey(tmp_path / f"auth-{marker}.key"),
                idle_seconds=30 * 60,
                absolute_seconds=12 * 60 * 60,
                reauthentication_seconds=5 * 60,
                max_sessions=5,
                failure_limit=5,
                lockout_seconds=60,
                password_hasher=hasher,
            )
            configured.add(marker)
        normalized_username = (username or account_id).lower()
        now = utc_now()
        with container.database.session(system=True) as session:
            account = session.get(UserRecord, account_id)
            if account is None:
                account = UserRecord(
                    id=account_id,
                    display_name=account_id,
                    created_at=now,
                )
                session.add(account)
            account.username = normalized_username
            account.password_hash = hasher.hash(password)
            account.role = role
            account.status = "active"
            account.failed_login_count = 0
            account.locked_until = None
            account.password_changed_at = now
            account.updated_at = now
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": normalized_username, "password": password},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        client.headers["X-CSRF-Token"] = payload["csrf_token"]
        return payload

    return authenticate
