from __future__ import annotations

from datetime import datetime
from typing import Any

from core.identity.models import AppliedPreference, PersonalContext
from core.identity.preference import (
    PreferenceEvidenceExtractor,
    preference_fingerprint,
)
from core.storage.repository import ExecutionStore


class PersonalizationService:
    """Builds personal context from evidence without modifying shared Skills."""

    def __init__(
        self,
        store: ExecutionStore,
        *,
        extractor: PreferenceEvidenceExtractor | None = None,
    ) -> None:
        self._store = store
        self._extractor = extractor or PreferenceEvidenceExtractor()

    def learn(
        self,
        *,
        user_id: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        task_ids = (
            [task_id]
            if task_id is not None
            else self._store.list_task_ids_for_user(user_id)
        )
        summaries = [
            self.learn_from_task(item, expected_user_id=user_id)
            for item in task_ids
        ]
        return {
            "user_id": user_id,
            "task_count": len(summaries),
            "sources_created": sum(
                item["sources_created"] for item in summaries
            ),
            "candidates_created": sum(
                item["candidates_created"] for item in summaries
            ),
            "evidence_links_created": sum(
                item["evidence_links_created"] for item in summaries
            ),
        }

    def learn_from_task(
        self,
        task_id: str,
        *,
        expected_user_id: str | None = None,
    ) -> dict[str, Any]:
        bundle = self._store.get_task_bundle(task_id)
        task = bundle["task"]
        user_id = str(task["user_id"])
        if expected_user_id is not None and user_id != expected_user_id:
            raise KeyError(f"TaskRecord not found: {task_id}")

        evidence_items = [
            self._extractor.from_feedback(task=task, feedback=item)
            for item in bundle["feedback"]
        ]
        evidence_items.extend(
            self._extractor.from_decision(task=task, decision=item)
            for item in bundle["decision_records"]
        )

        sources_created = 0
        candidates_created = 0
        evidence_links_created = 0
        for evidence in evidence_items:
            source_result = self._store.upsert_memory_source(
                user_id=evidence.user_id,
                task_id=evidence.task_id,
                source_type=evidence.source_type,
                source_id=evidence.source_id,
                source_kind=evidence.source_kind,
                content=evidence.content,
                captured_at=datetime.fromisoformat(evidence.captured_at),
            )
            sources_created += int(source_result["created"])
            if evidence.candidate is None:
                continue
            candidate = evidence.candidate
            preference_result = self._store.upsert_preference_candidate(
                user_id=evidence.user_id,
                context=candidate.context,
                category=candidate.category,
                rule=candidate.rule,
                fingerprint=preference_fingerprint(
                    context=candidate.context,
                    category=candidate.category,
                    rule=candidate.rule,
                ),
                memory_source_id=source_result["source"]["id"],
                extraction_method=candidate.extraction_method,
                weight=candidate.weight,
            )
            candidates_created += int(preference_result["created"])
            evidence_links_created += int(
                preference_result["evidence_added"]
            )

        return {
            "task_id": task_id,
            "user_id": user_id,
            "sources_created": sources_created,
            "candidates_created": candidates_created,
            "evidence_links_created": evidence_links_created,
        }

    def add_feedback(
        self,
        task_id: str,
        *,
        comment: str,
        rating: int | None,
    ) -> dict[str, Any]:
        feedback_id = self._store.add_feedback(
            task_id,
            comment=comment,
            rating=rating,
        )
        task = self._store.get_task_for_execution(task_id)
        learning = self.learn_from_task(
            task_id,
            expected_user_id=str(task["user_id"]),
        )
        return {
            "feedback_id": feedback_id,
            "task_id": task_id,
            "preference_learning": learning,
        }

    def for_task(
        self,
        *,
        user_id: str,
        context: str,
    ) -> PersonalContext:
        preferences = self._store.list_confirmed_preferences(
            user_id=user_id,
            context=context,
        )
        return PersonalContext(
            user_id=user_id,
            context=context,
            preferences=[
                AppliedPreference(
                    preference_id=item["id"],
                    context=item["context"],
                    category=item["category"],
                    rule=item["rule"],
                    confidence=item["confidence"],
                    evidence_count=item["evidence_count"],
                )
                for item in preferences
            ],
        )
