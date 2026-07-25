from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from adapters.github.models import GitHubIssue, RepositorySnapshot
from core.agents.runtime import AgentResult, AgentRuntime

_LABEL_WEIGHTS = {
    "security": 100,
    "critical": 90,
    "p0": 90,
    "priority: critical": 90,
    "priority: high": 50,
    "p1": 50,
    "high priority": 50,
    "bug": 25,
    "regression": 35,
    "data loss": 80,
    "documentation": 5,
    "good first issue": 3,
}


class RuleBasedRuntime(AgentRuntime):
    """Deterministic offline runtime used to make the MVP testable.

    It only derives claims from a repository snapshot. A model-backed runtime
    can replace it without changing skills, workflows, persistence, or the API.
    """

    async def run(
        self,
        task: str,
        context: dict[str, Any],
        tools: list[str],
    ) -> AgentResult:
        snapshot = RepositorySnapshot.model_validate(context["repository_snapshot"])
        if task == "project-daily-brief":
            output = self._daily_brief(snapshot)
        elif task == "issue-triage":
            output = self._issue_triage(snapshot)
        else:
            raise ValueError(f"RuleBasedRuntime does not implement task {task}")
        return AgentResult(
            output=output,
            runtime="rules-v1",
            metadata={
                "evidence_only": True,
                "tools_authorized": tools,
            },
        )

    async def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "runtime": "rules-v1",
            "remote": False,
        }

    def _daily_brief(self, snapshot: RepositorySnapshot) -> dict[str, Any]:
        now = snapshot.fetched_at.astimezone(UTC)
        recent_issues = sorted(snapshot.issues, key=lambda item: item.updated_at, reverse=True)
        recent_pulls = sorted(
            snapshot.pull_requests,
            key=lambda item: item.updated_at,
            reverse=True,
        )
        stale_issues = [
            issue
            for issue in snapshot.issues
            if (now - issue.updated_at.astimezone(UTC)).days >= 30
        ]
        security_issues = [
            issue
            for issue in snapshot.issues
            if "security" in {label.lower() for label in issue.labels}
        ]

        highlights: list[dict[str, Any]] = []
        for pull in recent_pulls[:3]:
            highlights.append(
                {
                    "type": "pull_request",
                    "number": pull.number,
                    "title": pull.title,
                    "detail": "草稿 PR" if pull.draft else "等待维护者关注的开放 PR",
                    "url": pull.html_url,
                }
            )
        for issue in recent_issues[: max(0, 3 - len(highlights))]:
            highlights.append(
                {
                    "type": "issue",
                    "number": issue.number,
                    "title": issue.title,
                    "detail": f"最近更新，已有 {issue.comments} 条讨论",
                    "url": issue.html_url,
                }
            )

        risks: list[dict[str, Any]] = []
        if security_issues:
            risks.append(
                {
                    "type": "security",
                    "message": f"有 {len(security_issues)} 个带 security 标签的开放 Issue。",
                    "evidence": [issue.html_url for issue in security_issues[:5]],
                }
            )
        if stale_issues:
            risks.append(
                {
                    "type": "stale_backlog",
                    "message": f"采样范围内有 {len(stale_issues)} 个 Issue 超过 30 天未更新。",
                    "evidence": [issue.html_url for issue in stale_issues[:5]],
                }
            )
        if not snapshot.pull_requests:
            risks.append(
                {
                    "type": "delivery_flow",
                    "message": "当前采样未发现开放 PR，需确认近期是否有可交付变更。",
                    "evidence": [snapshot.html_url],
                }
            )

        ranked = sorted(
            snapshot.issues,
            key=lambda issue: (-self._issue_score(issue, now), issue.number),
        )
        recommended_actions = [
            {
                "action": self._recommended_action(issue),
                "issue_number": issue.number,
                "title": issue.title,
                "url": issue.html_url,
            }
            for issue in ranked[:3]
        ]
        if recent_pulls:
            recommended_actions.insert(
                0,
                {
                    "action": "审阅最近更新的开放 PR",
                    "pull_request_number": recent_pulls[0].number,
                    "title": recent_pulls[0].title,
                    "url": recent_pulls[0].html_url,
                },
            )

        evidence_urls = [snapshot.html_url]
        evidence_urls.extend(item["url"] for item in highlights)
        evidence_urls.extend(
            url for risk in risks for url in risk.get("evidence", [])
        )
        return {
            "repository": snapshot.repository,
            "generated_at": now.isoformat(),
            "summary": (
                f"{snapshot.repository} 当前采样到 {len(snapshot.issues)} 个开放 Issue、"
                f"{len(snapshot.pull_requests)} 个开放 PR；"
                f"{len(stale_issues)} 个 Issue 超过 30 天未更新。"
            ),
            "health": {
                "sampled_open_issues": len(snapshot.issues),
                "sampled_open_pull_requests": len(snapshot.pull_requests),
                "stale_issue_count": len(stale_issues),
                "security_issue_count": len(security_issues),
                "stars": snapshot.stars,
                "forks": snapshot.forks,
            },
            "highlights": highlights,
            "risks": risks,
            "recommended_actions": recommended_actions,
            "evidence": sorted(set(evidence_urls)),
        }

    def _issue_triage(self, snapshot: RepositorySnapshot) -> dict[str, Any]:
        now = snapshot.fetched_at.astimezone(UTC)
        ranked = sorted(
            snapshot.issues,
            key=lambda issue: (-self._issue_score(issue, now), issue.number),
        )
        recommendations = []
        for issue in ranked:
            score = self._issue_score(issue, now)
            recommendations.append(
                {
                    "issue_number": issue.number,
                    "title": issue.title,
                    "priority": self._priority(score),
                    "score": score,
                    "rationale": self._rationale(issue, now),
                    "recommended_action": self._recommended_action(issue),
                    "evidence": issue.html_url,
                }
            )
        return {
            "repository": snapshot.repository,
            "total_open_issues": len(snapshot.issues),
            "recommendations": recommendations,
            "scoring_policy": {
                "label_signal": "安全、严重级别、回归和 bug 标签提高优先级",
                "community_signal": "讨论数和 reaction 数提高优先级",
                "activity_signal": "最近 7 天更新会小幅提高优先级",
                "note": "分数仅用于建议排序，最终决定必须由用户确认",
            },
        }

    @staticmethod
    def _issue_score(issue: GitHubIssue, now: datetime) -> int:
        labels = {label.lower().strip() for label in issue.labels}
        label_score = max((_LABEL_WEIGHTS.get(label, 0) for label in labels), default=0)
        discussion_score = min(issue.comments, 20) + min(issue.reactions * 2, 20)
        age_days = max(0, (now - issue.created_at.astimezone(UTC)).days)
        activity_days = max(0, (now - issue.updated_at.astimezone(UTC)).days)
        activity_score = 10 if activity_days <= 7 else 0
        longevity_score = min(age_days // 30, 10)
        return label_score + discussion_score + activity_score + longevity_score

    @staticmethod
    def _priority(score: int) -> str:
        if score >= 80:
            return "P0"
        if score >= 45:
            return "P1"
        if score >= 20:
            return "P2"
        return "P3"

    @staticmethod
    def _rationale(issue: GitHubIssue, now: datetime) -> list[str]:
        reasons: list[str] = []
        labels = [label.lower().strip() for label in issue.labels]
        weighted = [label for label in labels if label in _LABEL_WEIGHTS]
        if weighted:
            reasons.append(f"优先级标签信号：{', '.join(weighted)}")
        if issue.comments or issue.reactions:
            reasons.append(
                f"社区信号：{issue.comments} 条评论、{issue.reactions} 个 reaction"
            )
        activity_days = max(0, (now - issue.updated_at.astimezone(UTC)).days)
        reasons.append(f"距离上次更新 {activity_days} 天")
        if not weighted and issue.comments == 0 and issue.reactions == 0:
            reasons.append("暂无高优先级标签或社区讨论信号")
        return reasons

    @staticmethod
    def _recommended_action(issue: GitHubIssue) -> str:
        labels = {label.lower().strip() for label in issue.labels}
        if "security" in labels or "data loss" in labels:
            return "立即确认影响范围、负责人和修复时限"
        if labels & {"bug", "regression"}:
            return "确认复现条件并安排修复负责人"
        if labels & {"documentation", "docs"}:
            return "确认文档范围后安排小批次修订"
        return "补充影响范围和验收标准后再确认优先级"
