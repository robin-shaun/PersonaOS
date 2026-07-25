from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from core.agents.employee import EmployeeCatalog
from core.agents.runtime import AgentResult, AgentRuntime
from core.skills.executor import (
    SkillExecutionTimeoutError,
    SkillExecutor,
    SkillPermissionError,
)
from core.skills.registry import SkillRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SlowRuntime(AgentRuntime):
    async def run(
        self,
        task: str,
        context: dict[str, Any],
        tools: list[str],
    ) -> AgentResult:
        await asyncio.sleep(1)
        return AgentResult(output={}, runtime="slow-test")


def test_every_skill_declares_operational_contract() -> None:
    registry = SkillRegistry.from_directory(PROJECT_ROOT / "data" / "skills")
    skills = registry.all()

    assert {skill.name for skill in skills} == {
        "issue-triage",
        "memory-candidate-extraction",
        "project-daily-brief",
        "text-ingestion",
    }
    for skill in skills:
        assert skill.description
        assert skill.version
        assert skill.input_schema
        assert skill.output_schema
        assert skill.required_permissions
        assert skill.timeout_seconds > 0
        assert skill.retry_policy.max_attempts >= 1
        assert skill.risk_level in {"low", "medium", "high", "critical"}
        assert skill.tests
        assert skill.examples
        assert isinstance(skill.dependencies, list)

    assert registry.get("project-daily-brief").requires_confirmation is True
    assert registry.get("issue-triage").requires_confirmation is True
    assert registry.get("memory-candidate-extraction").requires_confirmation is False


@pytest.mark.asyncio
async def test_skill_permissions_and_timeout_are_enforced() -> None:
    loaded_registry = SkillRegistry.from_directory(PROJECT_ROOT / "data" / "skills")
    definition = loaded_registry.get("project-daily-brief").model_copy(
        update={"timeout_seconds": 0.01}
    )
    registry = SkillRegistry([definition])
    employee = EmployeeCatalog.from_directory(
        PROJECT_ROOT / "data" / "employee_templates"
    ).get("github-maintainer-001")
    executor = SkillExecutor(registry, SlowRuntime())

    without_permission = employee.model_copy(update={"allowed_permissions": []})
    with pytest.raises(SkillPermissionError, match="lacks permissions"):
        await executor.execute(
            definition.name,
            employee=without_permission,
            context={},
        )

    with pytest.raises(SkillExecutionTimeoutError, match="exceeded"):
        await executor.execute(
            definition.name,
            employee=employee,
            context={},
        )
