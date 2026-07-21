from __future__ import annotations

from dataclasses import dataclass

from adapters.github.client import HttpGitHubGateway
from adapters.github.models import GitHubGateway
from adapters.runtime.rule_based import RuleBasedRuntime
from core.agents.employee import EmployeeCatalog
from core.agents.runtime import AgentRuntime
from core.config import Settings
from core.evaluation.task_eval import ProjectMaintenanceEvaluator
from core.services.project_maintenance import (
    ApprovalService,
    ProjectMaintenanceService,
)
from core.skills.executor import SkillExecutor
from core.skills.registry import SkillRegistry
from core.storage.database import Database
from core.storage.repository import ExecutionStore
from core.workflows.models import WorkflowCatalog


@dataclass(slots=True)
class Container:
    settings: Settings
    database: Database
    store: ExecutionStore
    employees: EmployeeCatalog
    skill_registry: SkillRegistry
    workflows: WorkflowCatalog
    project_maintenance: ProjectMaintenanceService
    approvals: ApprovalService


def build_container(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    github: GitHubGateway | None = None,
    runtime: AgentRuntime | None = None,
) -> Container:
    settings = settings or Settings.from_env()
    (settings.base_dir / "var").mkdir(parents=True, exist_ok=True)

    employees = EmployeeCatalog.from_directory(settings.employee_config_dir)
    skill_registry = SkillRegistry.from_directory(settings.skill_config_dir)
    workflows = WorkflowCatalog.from_directory(settings.workflow_config_dir)

    database = database or Database(settings.database_url)
    database.create_schema()
    store = ExecutionStore(database)
    store.seed_definitions(
        employees=employees.all(),
        skills=skill_registry.all(),
        workflows=workflows.all(),
    )

    if runtime is None:
        if settings.runtime_name != "rules":
            raise ValueError(
                "Only the built-in rules runtime can be auto-configured. "
                "Inject HermesRuntime when DIGITAL_EMPLOYEE_RUNTIME=hermes."
            )
        runtime = RuleBasedRuntime()
    github = github or HttpGitHubGateway(
        token=settings.github_token,
        api_url=settings.github_api_url,
    )
    skill_executor = SkillExecutor(skill_registry, runtime)
    project_maintenance = ProjectMaintenanceService(
        store=store,
        employees=employees,
        workflows=workflows,
        skills=skill_executor,
        github=github,
        evaluator=ProjectMaintenanceEvaluator(),
        queue_max_attempts=settings.queue_max_attempts,
    )
    return Container(
        settings=settings,
        database=database,
        store=store,
        employees=employees,
        skill_registry=skill_registry,
        workflows=workflows,
        project_maintenance=project_maintenance,
        approvals=ApprovalService(store),
    )
