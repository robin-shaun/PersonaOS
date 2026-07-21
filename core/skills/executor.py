from __future__ import annotations

from typing import Any

from core.agents.employee import EmployeeDefinition
from core.agents.runtime import AgentResult, AgentRuntime
from core.skills.registry import SkillRegistry


class SkillPermissionError(PermissionError):
    pass


class SkillExecutor:
    def __init__(self, registry: SkillRegistry, runtime: AgentRuntime) -> None:
        self._registry = registry
        self._runtime = runtime

    async def execute(
        self,
        skill_name: str,
        *,
        employee: EmployeeDefinition,
        context: dict[str, Any],
    ) -> AgentResult:
        if skill_name not in employee.skills:
            raise SkillPermissionError(
                f"Employee {employee.employee_id} is not assigned skill {skill_name}"
            )

        definition = self._registry.get(skill_name)
        denied = set(definition.required_tools) - set(employee.allowed_tools)
        if denied:
            denied_tools = ", ".join(sorted(denied))
            raise SkillPermissionError(
                f"Employee {employee.employee_id} cannot use required tools: {denied_tools}"
            )

        runtime_context = {
            **context,
            "skill": definition.model_dump(mode="json"),
            "employee": employee.model_dump(mode="json"),
        }
        return await self._runtime.run(
            task=definition.name,
            context=runtime_context,
            tools=definition.required_tools,
        )

