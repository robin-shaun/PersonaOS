from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class AgentResult(BaseModel):
    output: dict[str, Any]
    runtime: str
    usage: dict[str, int | float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRuntime(ABC):
    """Stable boundary between business logic and an agent framework."""

    @abstractmethod
    async def run(
        self,
        task: str,
        context: dict[str, Any],
        tools: list[str],
    ) -> AgentResult:
        raise NotImplementedError

    async def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "runtime": self.__class__.__name__,
            "remote": False,
        }
