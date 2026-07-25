from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from core.ingestion.chunking import DeterministicTextChunker
from core.ingestion.extractor import MemoryCandidateExtractor
from core.ingestion.models import ChunkDraft, MemoryCandidateDraft
from core.security.access import AccessContext
from core.services.personas import (
    PERSONA_INGESTION_EMPLOYEE_ID,
    PERSONA_INGESTION_WORKFLOW,
)
from core.services.project_maintenance import (
    TaskCancellationRequested,
    TaskExecutionFailed,
)
from core.storage.blob import BlobStore
from core.storage.persona_repository import PersonaRepository
from core.storage.repository import ExecutionStore
from core.workflows.engine import StepResult, WorkflowEngine, WorkflowExecutionError
from core.workflows.models import (
    WorkflowCatalog,
    WorkflowStepDefinition,
)


class KnowledgeIngestionPersistenceError(RuntimeError):
    pass


class KnowledgeIngestionService:
    """Runs source ingestion without copying raw text into workflow state."""

    def __init__(
        self,
        *,
        store: ExecutionStore,
        personas: PersonaRepository,
        workflows: WorkflowCatalog,
        blob_store: BlobStore,
        chunker: DeterministicTextChunker,
        extractor: MemoryCandidateExtractor,
    ) -> None:
        self._store = store
        self._personas = personas
        self._workflows = workflows
        self._blob_store = blob_store
        self._chunker = chunker
        self._extractor = extractor

    async def run_task(self, task_id: str) -> dict[str, Any]:
        task = self._store.get_task_for_execution(task_id)
        if task["status"] == "cancelling":
            raise TaskCancellationRequested(task_id)
        if task["workflow_name"] != PERSONA_INGESTION_WORKFLOW:
            raise ValueError(
                f"Knowledge ingestion cannot run workflow {task['workflow_name']}"
            )
        if task["employee_id"] != PERSONA_INGESTION_EMPLOYEE_ID:
            raise PermissionError(
                "Knowledge ingestion task is assigned to an unauthorized employee"
            )
        task_input = task["input"]
        document_id = str(task_input["document_id"])
        persona_id = str(task_input["persona_id"])
        owner_id = str(task["user_id"])
        document = self._personas.get_document_for_processing(
            owner_id=owner_id,
            document_id=document_id,
        )
        if (
            document["persona_id"] != persona_id
            or document["content_sha256"] != task_input["content_sha256"]
            or document["task_id"] != task_id
        ):
            raise ValueError("ingestion task does not match its source document")

        workflow = self._workflows.get(PERSONA_INGESTION_WORKFLOW)
        plan = [
            {
                "step_id": step.id,
                "uses": step.uses,
                "retries": step.retries,
            }
            for step in workflow.steps
        ]
        task_run_id = self._store.start_task_run(task_id=task_id, plan=plan)
        initial_state = {
            "task": {
                "task_id": task_id,
                "task_run_id": task_run_id,
                "document_id": document_id,
                "persona_id": persona_id,
                "owner_id": owner_id,
            },
            "source": {
                "content_sha256": document["content_sha256"],
                "byte_size": document["byte_size"],
                "media_type": document["media_type"],
            },
        }
        workflow_run_id = self._store.create_workflow_run(
            task_run_id=task_run_id,
            workflow_name=workflow.name,
            workflow_version=workflow.version,
            initial_state=initial_state,
        )
        access = AccessContext(
            owner_id=owner_id,
            actor_id="knowledge-ingestion-worker",
            actor_type="system_worker",
            correlation_id=task_id,
        )
        self._personas.mark_document_processing(
            owner_id=owner_id,
            document_id=document_id,
        )

        working: dict[str, Any] = {}
        handlers = self._handlers(
            access=access,
            document=document,
            task_id=task_id,
            task_run_id=task_run_id,
            working=working,
        )
        engine = WorkflowEngine(handlers)

        def checkpoint(
            status: str,
            current_step: str | None,
            state: dict[str, Any],
            history: list[dict[str, Any]],
        ) -> None:
            self._store.checkpoint_workflow(
                workflow_run_id,
                status=status,
                current_step=current_step,
                state=state,
                history=history,
            )

        try:
            result = await engine.run(
                workflow,
                initial_state=initial_state,
                checkpoint=checkpoint,
            )
            output = result.state["persist_candidates"]
            completed = self._store.mark_execution_completed(
                task_id=task_id,
                task_run_id=task_run_id,
                workflow_run_id=workflow_run_id,
                output=output,
            )
            if not completed:
                raise TaskCancellationRequested(task_id)
            return self._store.get_task_bundle(task_id)
        except TaskCancellationRequested:
            raise
        except asyncio.CancelledError:
            self._personas.mark_document_failed(
                access=access,
                document_id=document_id,
                task_run_id=task_run_id,
                error="Task cancelled or timed out",
            )
            raise
        except Exception as exc:
            if self._store.is_task_cancellation_requested(task_id):
                raise TaskCancellationRequested(task_id) from exc
            cause = exc.cause if isinstance(exc, WorkflowExecutionError) else exc
            safe_error = f"{cause.__class__.__name__}: knowledge ingestion failed"
            self._personas.mark_document_failed(
                access=access,
                document_id=document_id,
                task_run_id=task_run_id,
                error=safe_error,
            )
            self._store.mark_execution_failed(
                task_id=task_id,
                task_run_id=task_run_id,
                workflow_run_id=workflow_run_id,
                error=safe_error,
            )
            raise TaskExecutionFailed(
                task_id,
                RuntimeError(safe_error),
            ) from exc

    def _handlers(
        self,
        *,
        access: AccessContext,
        document: dict[str, Any],
        task_id: str,
        task_run_id: str,
        working: dict[str, Any],
    ) -> dict[str, Any]:
        async def read_source(
            _: dict[str, Any],
            __: WorkflowStepDefinition,
        ) -> StepResult:
            started = perf_counter()
            tool_input = {
                "document_id": document["id"],
                "content_sha256": document["content_sha256"],
                "operation": "read_encrypted_blob",
            }
            try:
                raw = self._blob_store.get(
                    str(document["object_key"]),
                    expected_sha256=str(document["content_sha256"]),
                )
                text = raw.decode("utf-8-sig")
                if "\x00" in text or not text.strip():
                    raise ValueError("source blob is not valid non-empty text")
                working["text"] = text
                output = {
                    "content_sha256": document["content_sha256"],
                    "byte_size": len(raw),
                    "encoding": "utf-8",
                }
                self._store.record_tool_call(
                    task_run_id=task_run_id,
                    tool_name="local_encrypted_blob_reader",
                    tool_input=tool_input,
                    status="completed",
                    output=output,
                    error=None,
                    latency_ms=int((perf_counter() - started) * 1000),
                )
                return StepResult(output=output)
            except Exception as exc:
                self._store.record_tool_call(
                    task_run_id=task_run_id,
                    tool_name="local_encrypted_blob_reader",
                    tool_input=tool_input,
                    status="failed",
                    output=None,
                    error=exc.__class__.__name__,
                    latency_ms=int((perf_counter() - started) * 1000),
                )
                raise

        async def split_text(
            _: dict[str, Any],
            __: WorkflowStepDefinition,
        ) -> StepResult:
            chunks = self._chunker.split(str(working["text"]))
            working["chunks"] = chunks
            return StepResult(
                output={
                    "document_id": document["id"],
                    "chunk_count": len(chunks),
                    "chunker": (f"{self._chunker.name}@{self._chunker.version}"),
                    "chunker_config_hash": self._chunker.config_hash,
                }
            )

        async def extract_candidates(
            _: dict[str, Any],
            __: WorkflowStepDefinition,
        ) -> StepResult:
            chunks = list(working["chunks"])
            candidates = self._extractor.extract(chunks)
            working["candidates"] = candidates
            return StepResult(
                output={
                    "document_id": document["id"],
                    "candidate_count": len(candidates),
                    "extractor": (f"{self._extractor.name}@{self._extractor.version}"),
                    "model_inferences_created": 0,
                }
            )

        async def persist_candidates(
            _: dict[str, Any],
            __: WorkflowStepDefinition,
        ) -> StepResult:
            if self._store.is_task_cancellation_requested(task_id):
                raise TaskCancellationRequested(task_id)
            chunks: list[ChunkDraft] = list(working["chunks"])
            candidates: list[MemoryCandidateDraft] = list(working["candidates"])
            try:
                output = self._personas.persist_ingestion(
                    access=access,
                    document_id=str(document["id"]),
                    chunks=chunks,
                    candidates=candidates,
                )
            except Exception as exc:
                raise KnowledgeIngestionPersistenceError(
                    f"memory persistence failed ({exc.__class__.__name__})"
                ) from exc
            working.clear()
            return StepResult(output=output)

        return {
            "tool.local_encrypted_blob_reader": read_source,
            "ingestion.deterministic_text_chunker": split_text,
            "memory.rules_candidate_extractor": extract_candidates,
            "memory.persist_candidates": persist_candidates,
        }
