"""
Kiro Hook Integration — Bridge between Kiro headless agent and the review pipeline.

These hooks are called by Kiro during the review lifecycle:
1. pre_review — gather context, detect language, load relevant files
2. on_review — execute the actual review (can use Kiro's native understanding + our tools)
3. post_review — post comments, set status, cleanup
"""

import os
import subprocess
import json
from typing import Optional

from providers import create_provider, PRInfo
from src.config import load_config
from src.diff_analyzer import analyze_diff, detect_language
from src.ai_client import AIClient


def pre_review(pr_info: dict, config_path: str = "kiro/review-config.yaml") -> dict:
    """
    Pre-review hook — called before Kiro starts its analysis.
    
    Gathers context that Kiro needs:
    - Changed files list
    - Detected languages
    - Related unchanged files (for context)
    - Dependency changes
    
    Args:
        pr_info: Dict with owner, repo, pr_number, branch
        config_path: Path to review config
    
    Returns:
        Context dict for Kiro to use during review
    """
    config = load_config(config_path)

    # Create provider
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITLAB_TOKEN", "")
    provider_name = pr_info.get("provider", "github")
    provider = create_provider(provider_name, token=token)

    # Fetch PR details
    pr = provider.get_pr_info(
        owner=pr_info["owner"],
        repo=pr_info["repo"],
        pr_number=pr_info["pr_number"]
    )

    # Get changed files
    changed_files = provider.get_changed_files(pr)
    diff_summary = analyze_diff(changed_files)

    # Detect primary language
    languages = list(diff_summary.languages)

    # Find related context files (unchanged files in same directories)
    context_files = _find_context_files(changed_files, max_files=10)

    return {
        "pr": {
            "owner": pr.owner,
            "repo": pr.repo,
            "number": pr.pr_number,
            "title": pr.title,
            "author": pr.author,
            "branch": pr.branch,
            "base_branch": pr.base_branch,
        },
        "changed_files": [f["filename"] for f in changed_files],
        "languages": languages,
        "has_dependency_changes": diff_summary.has_dependency_changes,
        "has_config_changes": diff_summary.has_config_changes,
        "relevant_scans": diff_summary.relevant_scans,
        "context_files": context_files,
        "diff_summary": {
            "total_additions": diff_summary.total_additions,
            "total_deletions": diff_summary.total_deletions,
            "file_count": len(diff_summary.files),
        }
    }


def on_review(context: dict, kiro_analysis: Optional[str] = None) -> dict:
    """
    Main review hook — combines Kiro's semantic analysis with our tool-based scanning.
    
    If Kiro provides its own analysis (deep code understanding), it's merged
    with our security tool results for a comprehensive review.
    
    Args:
        context: Output from pre_review
        kiro_analysis: Optional natural language analysis from Kiro
    
    Returns:
        Combined review results
    """
    config = load_config("kiro/review-config.yaml")

    ai_client = AIClient(
        provider=config.ai.provider,
        model=config.ai.model,
        temperature=config.ai.temperature,
        max_tokens=config.ai.max_tokens,
    )

    # Build diff data from context
    diff_data = {
        "files": [],  # Would be populated from actual diff
        "total_additions": context["diff_summary"]["total_additions"],
        "total_deletions": context["diff_summary"]["total_deletions"],
        "languages": context["languages"],
        "has_dependency_changes": context["has_dependency_changes"],
    }

    # If Kiro provided its own analysis, use it as additional context
    kiro_context = ""
    if kiro_analysis:
        kiro_context = f"## Kiro Deep Analysis\n\n{kiro_analysis}"

    # Run AI review with Kiro context
    result = ai_client.review(diff_data, context=kiro_context)

    return result


def post_review(pr_info: dict, review_result: dict) -> dict:
    """
    Post-review hook — posts comments and sets status.
    
    Args:
        pr_info: Dict with owner, repo, pr_number
        review_result: Output from on_review
    
    Returns:
        Posting results (comment URLs, status)
    """
    config = load_config("kiro/review-config.yaml")

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITLAB_TOKEN", "")
    provider_name = pr_info.get("provider", "github")
    provider = create_provider(provider_name, token=token)

    pr = provider.get_pr_info(
        owner=pr_info["owner"],
        repo=pr_info["repo"],
        pr_number=pr_info["pr_number"]
    )

    # Import here to avoid circular
    from src.reviewer import PRReviewer
    from providers.base import ReviewComment

    # Post summary comment
    comment_body = _format_kiro_comment(review_result)
    comment_result = provider.post_comment(pr, ReviewComment(body=comment_body))

    # Set status
    risk = review_result.get("risk_level", "unknown")
    state = "success" if risk in ("low", "unknown") else "failure" if risk in ("critical", "high") else "success"
    status_result = provider.set_status(pr, state, f"Risk: {risk.upper()}")

    return {
        "comment_posted": comment_result.get("success", False),
        "comment_url": comment_result.get("comment_url", ""),
        "status_set": status_result.get("success", False),
        "status_state": state
    }


def _find_context_files(changed_files: list[dict], max_files: int = 10) -> list[str]:
    """Find related unchanged files in the same directories as changed files."""
    directories = set()
    for f in changed_files:
        parts = f["filename"].rsplit("/", 1)
        if len(parts) > 1:
            directories.add(parts[0])

    # Return directory list (Kiro will read the actual files)
    return list(directories)[:max_files]


def _format_kiro_comment(result: dict) -> str:
    """Format review result as a PR comment."""
    risk = result.get("risk_level", "unknown").upper()
    summary = result.get("summary", "Review complete.")
    risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴", "CRITICAL": "🔴"}.get(risk, "⚪")

    return f"""## 🛡️ AI Code Review (Kiro)

{risk_emoji} **Risk Level: {risk}**

> {summary}

_Powered by [AI PR Reviewer](https://github.com/aquavis12/AI-PR-Reviewer) + Kiro_"""
