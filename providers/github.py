"""
GitHub provider — fetches PR info, diffs, posts review comments.
"""

import hashlib
import hmac
from typing import Optional

import requests

from providers.base import GitProvider, PRInfo, ReviewComment


class GitHubProvider(GitProvider):
    """GitHub API integration for PR reviews."""

    API_BASE = "https://api.github.com"

    def __init__(self, token: str, webhook_secret: str = ""):
        self.token = token
        self.webhook_secret = webhook_secret
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

    def get_pr_info(self, owner: str, repo: str, pr_number: int) -> PRInfo:
        """Fetch PR metadata from GitHub API."""
        url = f"{self.API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        pr = resp.json()

        return PRInfo(
            provider="github",
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            branch=pr["head"]["ref"],
            base_branch=pr["base"]["ref"],
            sha=pr["head"]["sha"],
            clone_url=pr["head"]["repo"]["clone_url"],
            title=pr["title"],
            author=pr["user"]["login"],
            diff_url=pr["diff_url"]
        )

    def get_pr_diff(self, pr: PRInfo) -> str:
        """Get unified diff for the PR."""
        url = f"{self.API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.pr_number}"
        headers = {**self.headers, "Accept": "application/vnd.github.v3.diff"}
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text

    def get_changed_files(self, pr: PRInfo) -> list[dict]:
        """Get list of changed files with patches."""
        url = f"{self.API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.pr_number}/files"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()

        files = []
        for f in resp.json():
            files.append({
                "filename": f["filename"],
                "status": f["status"],  # added, modified, removed
                "additions": f["additions"],
                "deletions": f["deletions"],
                "patch": f.get("patch", ""),
                "raw_url": f.get("raw_url", "")
            })
        return files

    def post_comment(self, pr: PRInfo, comment: ReviewComment) -> dict:
        """Post a general comment on the PR."""
        url = f"{self.API_BASE}/repos/{pr.owner}/{pr.repo}/issues/{pr.pr_number}/comments"
        resp = requests.post(url, headers=self.headers, json={"body": comment.body})

        if resp.status_code == 201:
            return {"success": True, "comment_url": resp.json().get("html_url")}
        return {"success": False, "status": resp.status_code, "error": resp.text}

    def post_inline_comment(self, pr: PRInfo, comment: ReviewComment) -> dict:
        """Post an inline review comment on a specific file/line."""
        url = f"{self.API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.pr_number}/comments"
        payload = {
            "body": comment.body,
            "commit_id": pr.sha,
            "path": comment.path,
            "line": comment.line,
            "side": comment.side
        }
        resp = requests.post(url, headers=self.headers, json=payload)

        if resp.status_code == 201:
            return {"success": True, "comment_url": resp.json().get("html_url")}
        return {"success": False, "status": resp.status_code, "error": resp.text}

    def set_status(self, pr: PRInfo, state: str, description: str, target_url: str = "") -> dict:
        """Set commit status check."""
        url = f"{self.API_BASE}/repos/{pr.owner}/{pr.repo}/statuses/{pr.sha}"
        payload = {
            "state": state,  # success, failure, pending, error
            "context": "AI PR Reviewer",
            "description": description[:140],
            "target_url": target_url
        }
        resp = requests.post(url, headers=self.headers, json=payload)
        return {"success": resp.status_code == 201, "state": state}

    def parse_webhook(self, payload: dict, headers: dict) -> Optional[PRInfo]:
        """Parse GitHub webhook payload. Returns None if not a reviewable PR event."""
        # Verify signature if webhook secret is configured
        if self.webhook_secret:
            signature = headers.get("X-Hub-Signature-256", "")
            if not self._verify_signature(payload, signature):
                return None

        event_type = headers.get("X-GitHub-Event", "")
        if event_type != "pull_request":
            return None

        action = payload.get("action", "")
        if action not in ("opened", "synchronize", "reopened"):
            return None

        pr = payload["pull_request"]
        return PRInfo(
            provider="github",
            owner=pr["base"]["repo"]["owner"]["login"],
            repo=pr["base"]["repo"]["name"],
            pr_number=payload["number"],
            branch=pr["head"]["ref"],
            base_branch=pr["base"]["ref"],
            sha=pr["head"]["sha"],
            clone_url=pr["head"]["repo"]["clone_url"],
            title=pr["title"],
            author=pr["user"]["login"]
        )

    def _verify_signature(self, payload: dict, signature: str) -> bool:
        """Verify GitHub webhook HMAC-SHA256 signature."""
        if not signature or not signature.startswith("sha256="):
            return False

        import json
        body = json.dumps(payload, separators=(",", ":")).encode()
        expected = "sha256=" + hmac.new(
            self.webhook_secret.encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
