from __future__ import annotations

from typing import Any, Protocol

from core.agents.runtime import AgentResult, AgentRuntime


class HermesClient(Protocol):
    """Narrow protocol implemented by the selected Hermes SDK integration."""

    async def execute(
        self,
        *,
        instruction: str,
        context: dict[str, Any],
        tools: list[str],
    ) -> dict[str, Any]: ...

    async def status(self) -> dict[str, Any]: ...


class HermesRuntime(AgentRuntime):
    """Keeps Hermes-specific objects outside the business core."""

    def __init__(self, client: HermesClient) -> None:
        self._client = client

    async def run(
        self,
        task: str,
        context: dict[str, Any],
        tools: list[str],
    ) -> AgentResult:
        response = await self._client.execute(
            instruction=task,
            context=context,
            tools=tools,
        )
        output = response.get("output")
        if not isinstance(output, dict):
            raise ValueError("Hermes runtime must return a structured output object")
        return AgentResult(
            output=output,
            runtime=str(response.get("runtime", "hermes")),
            usage=dict(response.get("usage") or {}),
            metadata=dict(response.get("metadata") or {}),
        )

    async def status(self) -> dict[str, Any]:
        return await self._client.status()
