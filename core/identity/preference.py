from __future__ import annotations

from hashlib import sha256
from typing import Any

from core.identity.models import PersonalEvidence, PreferenceCandidateInput

_MISSING = object()


def preference_fingerprint(
    *,
    context: str,
    category: str,
    rule: str,
) -> str:
    normalized_rule = " ".join(rule.split()).casefold()
    value = "\0".join(
        (context.strip().casefold(), category.strip().casefold(), normalized_rule)
    )
    return sha256(value.encode("utf-8")).hexdigest()


def json_changes(
    original: Any,
    revised: Any,
    *,
    max_changes: int = 100,
) -> list[dict[str, Any]]:
    """Return a bounded structural diff suitable for behavior evidence."""

    changes: list[dict[str, Any]] = []

    def visit(before: Any, after: Any, path: str) -> None:
        if len(changes) >= max_changes or before == after:
            return
        if isinstance(before, dict) and isinstance(after, dict):
            keys = sorted(set(before) | set(after))
            for key in keys:
                child_path = f"{path}.{key}" if path else str(key)
                visit(
                    before.get(key, _MISSING),
                    after.get(key, _MISSING),
                    child_path,
                )
            return
        if isinstance(before, list) and isinstance(after, list):
            changes.append(
                {
                    "path": path or "$",
                    "operation": "replace",
                    "before": _bounded_value(before),
                    "after": _bounded_value(after),
                }
            )
            return
        if before is _MISSING:
            operation = "add"
        elif after is _MISSING:
            operation = "remove"
        else:
            operation = "replace"
        changes.append(
            {
                "path": path or "$",
                "operation": operation,
                **(
                    {"before": _bounded_value(before)}
                    if before is not _MISSING
                    else {}
                ),
                **(
                    {"after": _bounded_value(after)}
                    if after is not _MISSING
                    else {}
                ),
            }
        )

    visit(original, revised, "")
    return changes


def _bounded_value(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 1000 else f"{value[:1000]}…"
    if isinstance(value, list):
        if len(value) <= 20:
            return [_bounded_value(item) for item in value]
        return {
            "type": "array",
            "length": len(value),
            "sample": [_bounded_value(item) for item in value[:20]],
        }
    if isinstance(value, dict):
        if len(value) <= 20:
            return {
                str(key): _bounded_value(item)
                for key, item in value.items()
            }
        keys = list(value)[:20]
        return {
            "type": "object",
            "key_count": len(value),
            "sample": {
                str(key): _bounded_value(value[key])
                for key in keys
            },
        }
    return value


class PreferenceEvidenceExtractor:
    """Converts immutable task evidence into reviewable preference candidates."""

    def from_feedback(
        self,
        *,
        task: dict[str, Any],
        feedback: dict[str, Any],
    ) -> PersonalEvidence:
        kind = str(feedback["kind"])
        comment = (feedback.get("comment") or "").strip()
        changes = (
            json_changes(feedback.get("original"), feedback.get("revised"))
            if kind == "user_edit"
            else []
        )
        candidate = self._feedback_candidate(
            context=str(task["workflow_name"]),
            kind=kind,
            comment=comment,
            changes=changes,
        )
        return PersonalEvidence(
            user_id=str(task["user_id"]),
            task_id=str(task["id"]),
            source_type="feedback",
            source_id=str(feedback["id"]),
            source_kind=kind,
            captured_at=str(feedback["created_at"]),
            content={
                "comment": comment or None,
                "rating": feedback.get("rating"),
                "changes": changes,
            },
            candidate=candidate,
        )

    def from_decision(
        self,
        *,
        task: dict[str, Any],
        decision: dict[str, Any],
    ) -> PersonalEvidence:
        return PersonalEvidence(
            user_id=str(task["user_id"]),
            task_id=str(task["id"]),
            source_type="decision_record",
            source_id=str(decision["id"]),
            source_kind=str(decision["user_choice"]),
            captured_at=str(decision["created_at"]),
            content={
                "context": decision.get("context") or {},
                "options": decision.get("options") or [],
                "user_choice": decision["user_choice"],
                "user_reason": decision.get("user_reason"),
            },
        )

    @staticmethod
    def _feedback_candidate(
        *,
        context: str,
        kind: str,
        comment: str,
        changes: list[dict[str, Any]],
    ) -> PreferenceCandidateInput | None:
        if comment:
            category_weights = {
                "explicit_feedback": ("general", 0.80),
                "user_edit": ("work_product", 0.75),
                "rejection": ("decision", 0.70),
            }
            category, weight = category_weights.get(kind, ("general", 0.65))
            return PreferenceCandidateInput(
                context=context,
                category=category,
                rule=comment,
                extraction_method=f"{kind}-comment-v1",
                weight=weight,
            )
        if kind != "user_edit" or not changes:
            return None

        visible_changes = changes[:8]
        fields = "、".join(
            f"{item['path']}（{item['operation']}）"
            for item in visible_changes
        )
        remainder = len(changes) - len(visible_changes)
        suffix = f"，另有 {remainder} 处修改" if remainder else ""
        return PreferenceCandidateInput(
            context=context,
            category="work_product",
            rule=f"在 {context} 的交付中，用户修改了：{fields}{suffix}。",
            extraction_method="user-edit-diff-v1",
            weight=0.45,
        )
