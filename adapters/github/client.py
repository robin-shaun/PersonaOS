from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from urllib.request import proxy_bypass

import httpx

from adapters.github.models import (
    GitHubIssue,
    GitHubPullRequest,
    RepositorySnapshot,
)


class GitHubAdapterError(RuntimeError):
    pass


def normalize_repository(repository: str) -> str:
    normalized = repository.strip().strip("/")
    parts = normalized.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("repository must use the owner/name format")
    return f"{parts[0]}/{parts[1]}"


def proxy_url_from_environment(target_url: str) -> str | None:
    """Resolve a proxy without leaking environment-specific types into httpx.

    Some desktop proxy tools expose the common but non-standard socks:// scheme.
    httpx expects socks5://, so normalize it while still honoring NO_PROXY.
    """

    hostname = urlparse(target_url).hostname
    if hostname and proxy_bypass(hostname):
        return None
    variable_names = (
        ("HTTPS_PROXY", "https_proxy")
        if target_url.lower().startswith("https://")
        else ("HTTP_PROXY", "http_proxy")
    )
    candidates = (*variable_names, "ALL_PROXY", "all_proxy")
    for name in candidates:
        value = (os.getenv(name) or "").strip()
        if not value:
            continue
        if value.lower().startswith("socks://"):
            return f"socks5://{value[len('socks://') :]}"
        return value
    return None


class HttpGitHubGateway:
    """Read-only GitHub REST adapter.

    This adapter intentionally implements only GET operations. Adding mutation
    methods requires a separate capability and approval design.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        api_url: str = "https://api.github.com",
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
        proxy_url: str | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._client = client
        self._timeout = timeout_seconds
        self._proxy_url = proxy_url or proxy_url_from_environment(self._api_url)
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "digital-employee-mvp/0.1",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    async def get_repository_snapshot(
        self,
        repository: str,
        *,
        max_items: int = 50,
    ) -> RepositorySnapshot:
        repository = normalize_repository(repository)
        max_items = max(1, min(max_items, 100))

        if self._client is not None:
            return await self._fetch(self._client, repository, max_items)

        async with httpx.AsyncClient(
            base_url=self._api_url,
            headers=self._headers,
            timeout=self._timeout,
            follow_redirects=True,
            proxy=self._proxy_url,
            trust_env=False,
        ) as client:
            return await self._fetch(client, repository, max_items)

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        repository: str,
        max_items: int,
    ) -> RepositorySnapshot:
        repo_data, issue_data, pull_data = await asyncio.gather(
            self._get(client, f"/repos/{repository}"),
            self._get(
                client,
                f"/repos/{repository}/issues",
                params={
                    "state": "open",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": max_items,
                },
            ),
            self._get(
                client,
                f"/repos/{repository}/pulls",
                params={
                    "state": "open",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": max_items,
                },
            ),
        )

        issues = [
            self._parse_issue(item)
            for item in issue_data
            if "pull_request" not in item
        ]
        pulls = [self._parse_pull_request(item) for item in pull_data]
        return RepositorySnapshot(
            repository=repository,
            description=repo_data.get("description") or "",
            html_url=repo_data["html_url"],
            default_branch=repo_data["default_branch"],
            stars=repo_data.get("stargazers_count", 0),
            forks=repo_data.get("forks_count", 0),
            watchers=repo_data.get("subscribers_count", repo_data.get("watchers_count", 0)),
            open_issues_reported=repo_data.get("open_issues_count", 0),
            issues=issues,
            pull_requests=pulls,
            fetched_at=datetime.now(UTC),
        )

    async def _get(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await client.get(path, params=params, headers=self._headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            message = ""
            try:
                message = exc.response.json().get("message", "")
            except (ValueError, AttributeError):
                message = exc.response.text[:200]
            raise GitHubAdapterError(
                f"GitHub returned HTTP {status} for {path}: {message or 'request failed'}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GitHubAdapterError(f"GitHub request failed for {path}: {exc}") from exc

    @staticmethod
    def _parse_issue(item: dict[str, Any]) -> GitHubIssue:
        labels = [
            label.get("name", "") if isinstance(label, dict) else str(label)
            for label in item.get("labels", [])
        ]
        return GitHubIssue(
            number=item["number"],
            title=item["title"],
            body=(item.get("body") or "")[:4000],
            labels=[label for label in labels if label],
            author=(item.get("user") or {}).get("login"),
            comments=item.get("comments", 0),
            reactions=(item.get("reactions") or {}).get("total_count", 0),
            state=item.get("state", "open"),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            html_url=item["html_url"],
        )

    @staticmethod
    def _parse_pull_request(item: dict[str, Any]) -> GitHubPullRequest:
        return GitHubPullRequest(
            number=item["number"],
            title=item["title"],
            author=(item.get("user") or {}).get("login"),
            draft=item.get("draft", False),
            comments=item.get("comments", 0) + item.get("review_comments", 0),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            html_url=item["html_url"],
        )
