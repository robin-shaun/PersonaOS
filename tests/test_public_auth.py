from __future__ import annotations

import json
from dataclasses import replace
from urllib.error import URLError
from urllib.parse import parse_qs

import httpx
import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from apps.api.main import create_app
from core.bootstrap import build_container
from core.security.auth_key import LocalAuthKey
from core.security.authentication import AuthenticationService
from core.security.public_auth import SlidingWindowRateLimiter, TurnstileVerifier
from core.storage.auth_repository import AuthRepository
from core.storage.database import Database
from core.storage.models import AuthEventRecord

PASSWORD = "test-strong-password-123"


def install_fast_auth(container, tmp_path) -> AuthenticationService:
    service = AuthenticationService(
        repository=AuthRepository(container.database),
        auth_key=LocalAuthKey(tmp_path / "public-auth.key"),
        idle_seconds=30 * 60,
        absolute_seconds=12 * 60 * 60,
        reauthentication_seconds=5 * 60,
        max_sessions=5,
        failure_limit=5,
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


class FakeTurnstileVerifier:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls: list[tuple[str, str | None]] = []

    async def verify(self, token: str, *, remote_ip: str | None = None) -> bool:
        self.calls.append((token, remote_ip))
        return self.accepted


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


@pytest.mark.asyncio
async def test_turnstile_verifier_submits_secret_token_and_remote_ip(
    monkeypatch,
) -> None:
    submitted: dict[str, list[str]] = {}

    def fake_urlopen(request, *, timeout):
        assert timeout == 3.0
        submitted.update(parse_qs(request.data.decode()))
        return FakeResponse({"success": True})

    monkeypatch.setattr("core.security.public_auth.urlopen", fake_urlopen)
    verifier = TurnstileVerifier("secret", timeout_seconds=3.0)

    assert await verifier.verify("one-time-token", remote_ip="203.0.113.10")
    assert submitted == {
        "secret": ["secret"],
        "response": ["one-time-token"],
        "remoteip": ["203.0.113.10"],
    }

    def unavailable(*_args, **_kwargs):
        raise URLError("offline")

    monkeypatch.setattr("core.security.public_auth.urlopen", unavailable)
    assert not await verifier.verify("one-time-token")


def test_public_registration_configuration_and_rate_limiter(container) -> None:
    with pytest.raises(ValueError, match="PERSONA_COOKIE_SECURE"):
        replace(
            container.settings,
            persona_public_registration_enabled=True,
            persona_turnstile_site_key="site",
            persona_turnstile_secret_key="secret",
        )
    with pytest.raises(ValueError, match="PERSONA_TURNSTILE_SITE_KEY"):
        replace(
            container.settings,
            persona_cookie_secure=True,
            persona_public_registration_enabled=True,
        )

    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=10)
    assert limiter.allow("address", now=0)
    assert limiter.allow("address", now=1)
    assert not limiter.allow("address", now=2)
    assert limiter.allow("address", now=11)


@pytest.mark.asyncio
async def test_public_registration_creates_only_member_and_secure_session(
    container,
    tmp_path,
) -> None:
    public_container = build_container(
        settings=replace(
            container.settings,
            persona_cookie_secure=True,
            persona_public_registration_enabled=True,
            persona_turnstile_site_key="site-key",
            persona_turnstile_secret_key="secret-key",
        ),
        database=Database("sqlite://"),
    )
    service = install_fast_auth(public_container, tmp_path)
    service.create_account(
        username="owner-admin",
        display_name="Owner Admin",
        password=PASSWORD,
        role="admin",
    )
    app = create_app(public_container)
    verifier = FakeTurnstileVerifier()
    app.state.turnstile_verifier = verifier

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://persona.example",
        headers={
            "Origin": "https://persona.example",
            "CF-Connecting-IP": "203.0.113.24",
        },
    ) as client:
        status_response = await client.get("/api/v1/auth/status")
        assert status_response.json() == {
            "mode": "public_registration",
            "setup_required": False,
            "cookie_secure": True,
            "local_only": False,
            "registration_enabled": True,
            "turnstile_site_key": "site-key",
        }
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "new-member",
                "display_name": "New Member",
                "password": PASSWORD,
                "turnstile_token": "verified-token",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["account"]["role"] == "member"
        assert response.json()["account"]["username"] == "new-member"
        cookie = response.headers["set-cookie"].lower()
        assert "secure" in cookie
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert verifier.calls == [("verified-token", "203.0.113.24")]

        rejected_role = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "attacker",
                "display_name": "Attacker",
                "password": PASSWORD,
                "turnstile_token": "another-token",
                "role": "admin",
            },
        )
        assert rejected_role.status_code == 422

    with public_container.database.session(system=True) as session:
        events = list(
            session.scalars(
                select(AuthEventRecord).where(
                    AuthEventRecord.action == "account.created"
                )
            )
        )
    public_event = next(
        event
        for event in events
        if event.detail.get("created_via") == "public_registration"
    )
    assert public_event.detail["role"] == "member"


@pytest.mark.asyncio
async def test_registration_cannot_bootstrap_admin(container, tmp_path) -> None:
    public_container = build_container(
        settings=replace(
            container.settings,
            persona_cookie_secure=True,
            persona_public_registration_enabled=True,
            persona_turnstile_site_key="site-key",
            persona_turnstile_secret_key="secret-key",
        ),
        database=Database("sqlite://"),
    )
    install_fast_auth(public_container, tmp_path)
    app = create_app(public_container)
    verifier = FakeTurnstileVerifier()
    app.state.turnstile_verifier = verifier

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://persona.example",
        headers={"Origin": "https://persona.example"},
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "first-user",
                "display_name": "First User",
                "password": PASSWORD,
                "turnstile_token": "token",
            },
        )
    assert response.status_code == 409
    assert verifier.calls == []
