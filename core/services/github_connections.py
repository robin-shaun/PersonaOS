from __future__ import annotations

from typing import Any

from adapters.github.client import normalize_repository
from adapters.github.models import GitHubAppProvider, GitHubGateway
from core.storage.repository import ExecutionStore


class GitHubAppNotConfiguredError(RuntimeError):
    pass


class GitHubConnectionService:
    def __init__(
        self,
        *,
        store: ExecutionStore,
        provider: GitHubAppProvider | None,
    ) -> None:
        self._store = store
        self._provider = provider

    @property
    def enabled(self) -> bool:
        return self._provider is not None

    async def connect(
        self,
        *,
        user_id: str,
        installation_id: int,
        repository: str,
    ) -> dict[str, Any]:
        provider = self._required_provider()
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise ValueError("user_id must not be empty")
        access = await provider.verify_repository_access(
            installation_id,
            normalize_repository(repository),
        )
        return self._store.upsert_github_connection(
            user_id=normalized_user_id,
            installation_id=access.installation_id,
            repository=access.repository,
            account_login=access.account_login,
            private=access.private,
            permissions=access.permissions,
            repository_selection=access.repository_selection,
            verified_at=access.verified_at,
        )

    def list(
        self,
        *,
        user_id: str,
        include_disconnected: bool = False,
    ) -> list[dict[str, Any]]:
        return self._store.list_github_connections(
            user_id=user_id.strip(),
            include_disconnected=include_disconnected,
        )

    def disconnect(
        self,
        connection_id: str,
        *,
        user_id: str,
    ) -> dict[str, Any]:
        return self._store.disconnect_github_connection(
            connection_id,
            user_id=user_id.strip(),
        )

    def resolve_gateway(
        self,
        connection_id: str,
        *,
        user_id: str,
        repository: str | None = None,
    ) -> tuple[str, GitHubGateway]:
        connection = self._store.get_github_connection(
            connection_id,
            user_id=user_id,
            require_active=True,
        )
        connected_repository = str(connection["repository"])
        if repository is not None:
            requested_repository = normalize_repository(repository)
            if requested_repository.casefold() != connected_repository.casefold():
                raise ValueError(
                    "repository does not match the selected GitHub connection"
                )
        provider = self._required_provider()
        gateway = provider.gateway_for_installation(
            int(connection["installation_id"]),
            connected_repository,
        )
        return connected_repository, gateway

    def _required_provider(self) -> GitHubAppProvider:
        if self._provider is None:
            raise GitHubAppNotConfiguredError(
                "GitHub App authentication is not configured"
            )
        return self._provider
