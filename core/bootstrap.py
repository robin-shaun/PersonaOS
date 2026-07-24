from __future__ import annotations

from dataclasses import dataclass

from adapters.github.app import GitHubAppClient
from adapters.github.client import HttpGitHubGateway
from adapters.github.models import GitHubAppProvider, GitHubGateway
from adapters.hermes.client import HermesApiClient
from adapters.hermes.runtime import HermesRuntime
from adapters.runtime.rule_based import RuleBasedRuntime
from core.agents.employee import EmployeeCatalog
from core.agents.runtime import AgentRuntime
from core.config import Settings
from core.evaluation.task_eval import ProjectMaintenanceEvaluator
from core.services.github_connections import GitHubConnectionService
from core.services.personalization import PersonalizationService
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
    runtime: AgentRuntime
    github_connections: GitHubConnectionService
    personalization: PersonalizationService
    project_maintenance: ProjectMaintenanceService
    approvals: ApprovalService


def build_container(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    github: GitHubGateway | None = None,
    github_app: GitHubAppProvider | None = None,
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
        if settings.runtime_name == "rules":
            runtime = RuleBasedRuntime()
        elif settings.runtime_name == "hermes":
            if settings.hermes_api_key is None:
                raise ValueError(
                    "HERMES_API_KEY is required when "
                    "DIGITAL_EMPLOYEE_RUNTIME=hermes"
                )
            runtime = HermesRuntime(
                HermesApiClient(
                    api_url=settings.hermes_api_url,
                    api_key=settings.hermes_api_key,
                    model=settings.hermes_model,
                    request_timeout_seconds=(
                        settings.hermes_request_timeout_seconds
                    ),
                    poll_interval_seconds=settings.hermes_poll_interval_seconds,
                    max_context_bytes=settings.hermes_max_context_bytes,
                )
            )
        else:
            raise ValueError(
                "DIGITAL_EMPLOYEE_RUNTIME must be rules or hermes"
            )
    github = github or HttpGitHubGateway(
        token=settings.github_token,
        api_url=settings.github_api_url,
        api_version=settings.github_api_version,
    )
    if github_app is None and settings.github_app_configured:
        if settings.github_app_id is None or settings.github_app_private_key is None:
            raise ValueError("GitHub App configuration is incomplete")
        github_app = GitHubAppClient(
            app_id=settings.github_app_id,
            private_key=settings.github_app_private_key,
            api_url=settings.github_api_url,
            api_version=settings.github_api_version,
        )
    github_connections = GitHubConnectionService(
        store=store,
        provider=github_app,
    )
    personalization = PersonalizationService(store)
    skill_executor = SkillExecutor(skill_registry, runtime)
    project_maintenance = ProjectMaintenanceService(
        store=store,
        employees=employees,
        workflows=workflows,
        skills=skill_executor,
        github=github,
        github_connections=github_connections,
        evaluator=ProjectMaintenanceEvaluator(),
        personal_context=personalization,
        queue_max_attempts=settings.queue_max_attempts,
    )
    return Container(
        settings=settings,
        database=database,
        store=store,
        employees=employees,
        skill_registry=skill_registry,
        workflows=workflows,
        runtime=runtime,
        github_connections=github_connections,
        personalization=personalization,
        project_maintenance=project_maintenance,
        approvals=ApprovalService(store, personalization),
    )
