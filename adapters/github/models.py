from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field


class GitHubIssue(BaseModel):
    number: int
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    author: str | None = None
    comments: int = 0
    reactions: int = 0
    state: str = "open"
    created_at: datetime
    updated_at: datetime
    html_url: str


class GitHubPullRequest(BaseModel):
    number: int
    title: str
    author: str | None = None
    draft: bool = False
    comments: int = 0
    created_at: datetime
    updated_at: datetime
    html_url: str


class RepositorySnapshot(BaseModel):
    repository: str
    description: str = ""
    html_url: str
    default_branch: str
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    open_issues_reported: int = 0
    issues: list[GitHubIssue] = Field(default_factory=list)
    pull_requests: list[GitHubPullRequest] = Field(default_factory=list)
    fetched_at: datetime


class GitHubGateway(Protocol):
    async def get_repository_snapshot(
        self,
        repository: str,
        *,
        max_items: int = 50,
    ) -> RepositorySnapshot: ...

