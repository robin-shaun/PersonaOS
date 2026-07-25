from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

from adapters.github.client import (
    GitHubAdapterError,
    HttpGitHubGateway,
    normalize_repository,
    proxy_url_from_environment,
)
from adapters.github.models import (
    GitHubGateway,
    GitHubRepositoryAccess,
    RepositorySnapshot,
)

_READ_ONLY_PERMISSIONS = {
    "issues": "read",
    "pull_requests": "read",
}


@dataclass(frozen=True, slots=True)
class InstallationAccessToken:
    token: str = field(repr=False)
    expires_at: datetime
    permissions: dict[str, str]
    repository_selection: str | None


class GitHubAppClient:
    """GitHub App authentication with repository-scoped, in-memory tokens."""

    def __init__(
        self,
        *,
        app_id: str,
        private_key: str,
        api_url: str = "https://api.github.com",
        api_version: str = "2026-03-10",
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
        proxy_url: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not app_id.strip():
            raise ValueError("GitHub App ID must not be empty")
        if not private_key.strip():
            raise ValueError("GitHub App private key must not be empty")
        self._app_id = app_id.strip()
        self._private_key = private_key
        self._api_url = api_url.rstrip("/")
        self._api_version = api_version
        self._client = client
        self._timeout = timeout_seconds
        self._proxy_url = proxy_url or proxy_url_from_environment(self._api_url)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_cache: dict[tuple[int, str], InstallationAccessToken] = {}
        self._token_lock = asyncio.Lock()

    def create_app_jwt(self) -> str:
        now = self._now()
        payload = {
            "iat": int((now - timedelta(seconds=60)).timestamp()),
            "exp": int((now + timedelta(minutes=9)).timestamp()),
            "iss": self._app_id,
        }
        try:
            return jwt.encode(payload, self._private_key, algorithm="RS256")
        except (ValueError, TypeError, jwt.PyJWTError) as exc:
            raise GitHubAdapterError("Unable to sign GitHub App JWT") from exc

    async def get_installation_token(
        self,
        installation_id: int,
        repository: str,
    ) -> InstallationAccessToken:
        if installation_id < 1:
            raise ValueError("installation_id must be positive")
        repository = normalize_repository(repository)
        cache_key = (installation_id, repository.casefold())
        refresh_before = self._now() + timedelta(minutes=5)
        cached = self._token_cache.get(cache_key)
        if cached is not None and cached.expires_at > refresh_before:
            return cached

        async with self._token_lock:
            now = self._now()
            for key, value in list(self._token_cache.items()):
                if value.expires_at <= now:
                    self._token_cache.pop(key, None)
            refresh_before = now + timedelta(minutes=5)
            cached = self._token_cache.get(cache_key)
            if cached is not None and cached.expires_at > refresh_before:
                return cached

            repository_name = repository.split("/", maxsplit=1)[1]
            payload = await self._request_json(
                "POST",
                f"/app/installations/{installation_id}/access_tokens",
                headers=self._app_headers(),
                json={
                    "repositories": [repository_name],
                    "permissions": _READ_ONLY_PERMISSIONS,
                },
            )
            try:
                token_value = payload["token"]
                if not isinstance(token_value, str) or not token_value:
                    raise ValueError("token must be a non-empty string")
                expires_at = self._parse_datetime(str(payload["expires_at"]))
                if expires_at <= self._now():
                    raise ValueError("token must expire in the future")
                raw_permissions = payload.get("permissions") or {}
                if not isinstance(raw_permissions, dict):
                    raise ValueError("permissions must be an object")
                repository_selection = payload.get("repository_selection")
                if repository_selection is not None and not isinstance(
                    repository_selection, str
                ):
                    raise ValueError("repository_selection must be a string")
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise GitHubAdapterError(
                    "GitHub returned an invalid installation token response"
                ) from exc
            permissions = {
                str(name): str(level) for name, level in raw_permissions.items()
            }
            token = InstallationAccessToken(
                token=token_value,
                expires_at=expires_at,
                permissions=permissions,
                repository_selection=repository_selection,
            )
            self._token_cache[cache_key] = token
            return token

    async def verify_repository_access(
        self,
        installation_id: int,
        repository: str,
    ) -> GitHubRepositoryAccess:
        requested_repository = normalize_repository(repository)
        token = await self.get_installation_token(
            installation_id,
            requested_repository,
        )
        missing_permissions = sorted(
            name
            for name in _READ_ONLY_PERMISSIONS
            if token.permissions.get(name) != "read"
        )
        if missing_permissions:
            raise GitHubAdapterError(
                "GitHub installation token did not grant required read-only permissions: "
                + ", ".join(missing_permissions)
            )
        payload = await self._request_json(
            "GET",
            f"/repos/{requested_repository}",
            headers=self._installation_headers(token.token),
        )
        try:
            full_name = payload["full_name"]
            account_login = payload["owner"]["login"]
            private = payload["private"]
            if not isinstance(full_name, str) or not full_name:
                raise ValueError("full_name must be a non-empty string")
            if not isinstance(account_login, str) or not account_login:
                raise ValueError("owner login must be a non-empty string")
            if not isinstance(private, bool):
                raise ValueError("private must be a boolean")
            canonical_repository = normalize_repository(full_name)
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubAdapterError(
                "GitHub returned an invalid repository response"
            ) from exc
        if canonical_repository.casefold() != requested_repository.casefold():
            raise GitHubAdapterError(
                "GitHub returned a different repository than the one requested"
            )
        return GitHubRepositoryAccess(
            installation_id=installation_id,
            repository=canonical_repository,
            account_login=account_login,
            private=private,
            permissions=token.permissions,
            repository_selection=token.repository_selection,
            verified_at=self._now(),
        )

    def gateway_for_installation(
        self,
        installation_id: int,
        repository: str,
    ) -> GitHubGateway:
        return InstallationGitHubGateway(
            app=self,
            installation_id=installation_id,
            repository=repository,
        )

    def _app_headers(self) -> dict[str, str]:
        return {
            **self._base_headers(),
            "Authorization": f"Bearer {self.create_app_jwt()}",
        }

    def _installation_headers(self, token: str) -> dict[str, str]:
        return {
            **self._base_headers(),
            "Authorization": f"Bearer {token}",
        }

    def _base_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self._api_version,
            "User-Agent": "personaos/0.7",
        }

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
    ) -> Any:
        if self._client is not None:
            return await self._send(
                self._client,
                method,
                path,
                headers=headers,
                json=json,
            )
        async with httpx.AsyncClient(
            base_url=self._api_url,
            timeout=self._timeout,
            follow_redirects=True,
            proxy=self._proxy_url,
            trust_env=False,
        ) as client:
            return await self._send(
                client,
                method,
                path,
                headers=headers,
                json=json,
            )

    @staticmethod
    async def _send(
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None,
    ) -> Any:
        try:
            response = await client.request(
                method,
                path,
                headers=headers,
                json=json,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            message = ""
            try:
                message = str(exc.response.json().get("message", ""))
            except (ValueError, AttributeError):
                message = exc.response.text[:200]
            raise GitHubAdapterError(
                f"GitHub returned HTTP {status} for {path}: "
                f"{message or 'request failed'}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GitHubAdapterError(
                f"GitHub request failed for {path}: {exc}"
            ) from exc

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


class InstallationGitHubGateway:
    def __init__(
        self,
        *,
        app: GitHubAppClient,
        installation_id: int,
        repository: str,
    ) -> None:
        self._app = app
        self._installation_id = installation_id
        self._repository = normalize_repository(repository)

    async def get_repository_snapshot(
        self,
        repository: str,
        *,
        max_items: int = 50,
    ) -> RepositorySnapshot:
        repository = normalize_repository(repository)
        if repository.casefold() != self._repository.casefold():
            raise PermissionError(
                "Installation gateway is bound to a different repository"
            )
        token = await self._app.get_installation_token(
            self._installation_id,
            self._repository,
        )
        gateway = HttpGitHubGateway(
            token=token.token,
            api_url=self._app._api_url,
            api_version=self._app._api_version,
            client=self._app._client,
            timeout_seconds=self._app._timeout,
            proxy_url=self._app._proxy_url,
        )
        return await gateway.get_repository_snapshot(
            self._repository,
            max_items=max_items,
        )
