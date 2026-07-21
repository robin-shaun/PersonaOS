from __future__ import annotations

from typing import Any

from adapters.github.models import RepositorySnapshot


class ProjectMaintenanceEvaluator:
    def evaluate(
        self,
        *,
        repository_snapshot: dict[str, Any],
        brief: dict[str, Any],
        triage: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = RepositorySnapshot.model_validate(repository_snapshot)
        valid_issue_urls = {item.html_url for item in snapshot.issues}
        valid_pull_urls = {item.html_url for item in snapshot.pull_requests}
        valid_urls = valid_issue_urls | valid_pull_urls | {snapshot.html_url}
        valid_issue_numbers = {item.number for item in snapshot.issues}

        required_brief_sections = {
            "repository",
            "summary",
            "health",
            "highlights",
            "risks",
            "recommended_actions",
            "evidence",
        }
        brief_sections_ok = required_brief_sections.issubset(brief)
        brief_evidence = set(brief.get("evidence") or [])
        brief_evidence_ok = bool(brief_evidence) and brief_evidence.issubset(valid_urls)

        recommendations = triage.get("recommendations") or []
        issue_references_ok = all(
            item.get("issue_number") in valid_issue_numbers
            and item.get("evidence") in valid_issue_urls
            for item in recommendations
        )
        rationale_ok = all(bool(item.get("rationale")) for item in recommendations)
        issue_coverage_ok = len(recommendations) == len(snapshot.issues)
        actions = brief.get("recommended_actions") or []
        actionability_ok = bool(actions) or (
            not snapshot.issues and not snapshot.pull_requests
        )

        checks = [
            {
                "name": "required_brief_sections",
                "passed": brief_sections_ok,
            },
            {
                "name": "brief_evidence_integrity",
                "passed": brief_evidence_ok,
            },
            {
                "name": "issue_reference_integrity",
                "passed": issue_references_ok,
            },
            {
                "name": "triage_rationale_coverage",
                "passed": rationale_ok,
            },
            {
                "name": "sampled_issue_coverage",
                "passed": issue_coverage_ok,
            },
            {
                "name": "actionability",
                "passed": actionability_ok,
            },
        ]
        passed_count = sum(1 for check in checks if check["passed"])
        score = round((passed_count / len(checks)) * 100, 1)
        return {
            "passed": all(check["passed"] for check in checks),
            "score": score,
            "checks": checks,
            "metrics": {
                "sampled_issue_count": len(snapshot.issues),
                "triaged_issue_count": len(recommendations),
                "evidence_url_count": len(brief_evidence),
                "recommended_action_count": len(actions),
            },
        }

