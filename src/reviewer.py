"""
Core Review Engine — Orchestrates the full PR review pipeline.

Flow: Provider → Diff Analysis → AI Review → Format → Post
"""

import time
from dataclasses import asdict

from providers.base import GitProvider, PRInfo, ReviewComment
from src.diff_analyzer import analyze_diff, filter_reviewable_files, DiffSummary
from src.ai_client import AIClient
from src.config import Config


class PRReviewer:
    """Main review orchestrator."""

    def __init__(self, config: Config, provider: GitProvider, ai_client: AIClient):
        self.config = config
        self.provider = provider
        self.ai = ai_client

    def review_pr(self, pr: PRInfo) -> dict:
        """
        Execute the full review pipeline for a PR.

        Returns:
            Complete review result with comment posting status
        """
        start_time = time.time()

        # 1. Set pending status
        if self.config.review.set_status:
            self.provider.set_status(pr, "pending", "AI review in progress...")

        # 2. Get changed files
        changed_files = self.provider.get_changed_files(pr)
        if not changed_files:
            return self._empty_result(pr, "No file changes detected")

        # 3. Analyze diff
        diff_summary = analyze_diff(changed_files)

        # 4. Filter files (skip docs, configs based on ignore_paths)
        reviewable = filter_reviewable_files(
            diff_summary.files,
            self.config.review.ignore_paths
        )

        if not reviewable:
            return self._empty_result(pr, "All changed files are excluded from review")

        # 5. Prepare diff data for AI
        diff_data = {
            "files": [
                {
                    "filename": f.filename,
                    "status": f.status,
                    "language": f.language,
                    "additions": f.additions,
                    "deletions": f.deletions,
                    "patch": f.patch[:5000],  # Truncate large patches
                    "risk_signals": f.risk_signals,
                }
                for f in reviewable
            ],
            "total_additions": diff_summary.total_additions,
            "total_deletions": diff_summary.total_deletions,
            "languages": list(diff_summary.languages),
            "has_dependency_changes": diff_summary.has_dependency_changes,
            "has_config_changes": diff_summary.has_config_changes,
            "relevant_scans": diff_summary.relevant_scans,
        }

        # 6. Run AI review
        ai_result = self.ai.review(diff_data)

        # 7. Format and post comment
        duration_ms = round((time.time() - start_time) * 1000)
        ai_result["duration_ms"] = duration_ms
        ai_result["pr"] = f"{pr.owner}/{pr.repo}#{pr.pr_number}"

        comment_result = {}
        if self.config.review.post_comment:
            comment_body = self._format_comment(ai_result, pr)
            comment_result = self.provider.post_comment(pr, ReviewComment(body=comment_body))

        # 8. Post inline comments
        inline_results = []
        for inline in ai_result.get("inline_comments", [])[:10]:  # Max 10 inline comments
            try:
                ic_result = self.provider.post_inline_comment(
                    pr,
                    ReviewComment(
                        body=f"**[{inline['severity'].upper()}]** {inline['comment']}",
                        path=inline["file"],
                        line=inline["line"]
                    )
                )
                inline_results.append(ic_result)
            except Exception:
                pass  # Inline comments can fail on line number mismatch

        # 9. Set final status
        if self.config.review.set_status:
            risk = ai_result.get("risk_level", "unknown")
            fail_threshold = self.config.review.fail_on
            state = self._determine_status(risk, fail_threshold)
            desc = f"Risk: {risk.upper()} — {ai_result.get('summary', '')[:100]}"
            self.provider.set_status(pr, state, desc)

        return {
            "scan_id": f"pr-{pr.owner}-{pr.repo}-{pr.pr_number}",
            "pr": f"{pr.owner}/{pr.repo}#{pr.pr_number}",
            "risk_level": ai_result.get("risk_level", "unknown"),
            "safe_to_merge": ai_result.get("safe_to_merge", True),
            "summary": ai_result.get("summary", ""),
            "findings": ai_result.get("findings", {}),
            "recommendations": ai_result.get("recommendations", []),
            "comment_posted": comment_result.get("success", False),
            "comment_url": comment_result.get("comment_url", ""),
            "inline_comments_posted": len([r for r in inline_results if r.get("success")]),
            "duration_ms": duration_ms
        }

    def _format_comment(self, ai_result: dict, pr: PRInfo) -> str:
        """Format AI results into a clean PR comment."""
        risk = ai_result.get("risk_level", "unknown").upper()
        summary = ai_result.get("summary", "No summary available.")
        findings = ai_result.get("findings", {})
        security = findings.get("security", {})
        quality = findings.get("quality", {})
        recommendations = ai_result.get("recommendations", [])
        duration = ai_result.get("duration_ms", 0)
        safe = ai_result.get("safe_to_merge", True)

        risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴", "CRITICAL": "🔴"}.get(risk, "⚪")
        merge_badge = "✅ Safe to merge" if safe else "⛔ Review required before merge"

        comment = f"""## 🛡️ AI Code Review

{risk_emoji} **Risk Level: {risk}** | {merge_badge}

> {summary}

---

### Security Findings

| Severity | Count |
|----------|-------|
| Critical | {security.get('critical', 0)} |
| High | {security.get('high', 0)} |
| Medium | {security.get('medium', 0)} |
| Low | {security.get('low', 0)} |

"""

        # Security details
        details = security.get("details", [])
        if details:
            comment += "**Details:**\n"
            for d in details[:5]:
                comment += f"- {d}\n"
            comment += "\n"

        # Quality
        if quality:
            comment += f"### Code Quality: **{quality.get('score', '-')}** ({quality.get('issues', 0)} issues)\n\n"

        # Recommendations
        if recommendations:
            comment += "### Recommendations\n\n"
            for i, rec in enumerate(recommendations[:5], 1):
                if isinstance(rec, str):
                    comment += f"{i}. {rec}\n"
                elif isinstance(rec, dict):
                    action = rec.get("action", "")
                    priority = rec.get("priority", "")
                    effort = rec.get("effort", "")
                    comment += f"{i}. **{action}**"
                    if priority:
                        comment += f" [{priority}]"
                    if effort:
                        comment += f" _(effort: {effort})_"
                    comment += "\n"

        comment += f"\n---\n"
        comment += f"_Duration: {duration/1000:.1f}s | "
        comment += f"Powered by [AI PR Reviewer](https://github.com/aquavis12/AI-PR-Reviewer)_"

        return comment

    def _determine_status(self, risk: str, fail_on: str) -> str:
        """Determine pass/fail status based on risk level and threshold."""
        risk_levels = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        risk_val = risk_levels.get(risk, 0)
        threshold_val = risk_levels.get(fail_on, 5)  # 'never' = 5 (never fail)

        if risk_val >= threshold_val:
            return "failure"
        return "success"

    def _empty_result(self, pr: PRInfo, reason: str) -> dict:
        """Return an empty/skip result."""
        if self.config.review.set_status:
            self.provider.set_status(pr, "success", reason)
        return {
            "pr": f"{pr.owner}/{pr.repo}#{pr.pr_number}",
            "risk_level": "low",
            "safe_to_merge": True,
            "summary": reason,
            "skipped": True
        }
