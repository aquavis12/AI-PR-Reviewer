"""
GitLab provider — fetches MR info, diffs, posts review notes.
"""

from typing import Optional

import requests

from providers.base import GitProvider, PRInfo, ReviewComment


class GitLabProvider(GitProvider):
    """GitLab API integration for Merge Request reviews."""

    def __init__(self, token: str, base_url: str = "https://gitlab.com", webhook_secret=[REDACTED_PASSWORD] = ""):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/v4"
        self.webhook_secret=[REDACTED_PASSWORD]
        self.headers = {
            "PRIVATE-TOKEN": token,
            "Content-Type": "application/json"
        }

    def _project_id(self, owner: str, repo: str) -> str:
        """URL-encode project path for GitLab API."""
        return requests.utils.quote(f"{owner}/{repo}", safe="")

    def get_pr_info(self, owner: str, repo: str, pr_number: int) -> PRInfo:
        """Fetch MR metadata from GitLab API."""
        project = self._project_id(owner, repo)
        url = f"{self.api_base}/projects/{project}/merge_requests/{pr_number}"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        mr = resp.json()

        # Get project clone URL
        project_url = f"{self.api_base}/projects/{project}"
        proj_resp = requests.get(project_url, headers=self.headers)
        clone_url = proj_resp.json().get("http_url_to_repo", f"{self.base_url}/{owner}/{repo}.git")

        return PRInfo(
            provider="gitlab",
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            branch=mr["source_branch"],
            base_branch=mr["target_branch"],
            sha=mr["sha"],
            clone_url=clone_url,
            title=mr["title"],
            author=mr["author"]["username"]
        )

    def get_pr_diff(self, pr: PRInfo) -> str:
        """Get unified diff for the MR."""
        project = self._project_id(pr.owner, pr.repo)
        url = f"{self.api_base}/projects/{project}/merge_requests/{pr.pr_number}/changes"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()

        # Reconstruct unified diff from changes
        changes = resp.json().get("changes", [])
        diff_parts = []
        for change in changes:
            diff_parts.append(change.get("diff", ""))
        return "\n".join(diff_parts)

    def get_changed_files(self, pr: PRInfo) -> list[dict]:
        """Get list of changed files with patches."""
        project = self._project_id(pr.owner, pr.repo)
        url = f"{self.api_base}/projects/{project}/merge_requests/{pr.pr_number}/changes"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()

        files = []
        for change in resp.json().get("changes", []):
            files.append({
                "filename": change["new_path"],
                "old_path": change["old_path"],
                "status": "added" if change["new_file"] else "modified" if not change["deleted_file"] else "removed",
                "additions": change.get("diff", "").count("\n+"),
                "deletions": change.get("diff", "").count("\n-"),
                "patch": change.get("diff", "")
            })
        return files

    def post_comment(self, pr: PRInfo, comment: ReviewComment) -> dict:
        """Post a general note on the MR."""
        project = self._project_id(pr.owner, pr.repo)
        url = f"{self.api_base}/projects/{project}/merge_requests/{pr.pr_number}/notes"
        resp = requests.post(url, headers=self.headers, json={"body": comment.body})

        if resp.status_code == 201:
            data = resp.json()
            note_url = f"{self.base_url}/{pr.owner}/{pr.repo}/-/merge_requests/{pr.pr_number}#note_{data['id']}"
            return {"success": True, "comment_url": note_url}
        return {"success": False, "status": resp.status_code, "error": resp.text}

    def post_inline_comment(self, pr: PRInfo, comment: ReviewComment) -> dict:
        """Post an inline discussion on a specific file/line."""
        project = self._project_id(pr.owner, pr.repo)
        url = f"{self.api_base}/projects/{project}/merge_requests/{pr.pr_number}/discussions"

        payload = {
            "body": comment.body,
            "position": {
                "base_sha": "",  # Would need MR versions API for this
                "start_sha": "",
                "head_sha": pr.sha,
                "position_type": "text",
                "new_path": comment.path,
                "new_line": comment.line
            }
        }
        resp = requests.post(url, headers=self.headers, json=payload)

        if resp.status_code == 201:
            return {"success": True, "discussion_id": resp.json().get("id")}
        return {"success": False, "status": resp.status_code, "error": resp.text}

    def set_status(self, pr: PRInfo, state: str, description: str, target_url: str = "") -> dict:
        """Set commit status on GitLab."""
        # Map to GitLab states: pending, running, success, failed, canceled
        state_map = {
            "success": "success",
            "failure": "failed",
            "pending": "pending",
            "error": "failed"
        }
        gl_state = state_map.get(state, state)

        project = self._project_id(pr.owner, pr.repo)
        url = f"{self.api_base}/projects/{project}/statuses/{pr.sha}"
        payload = {
            "state": gl_state,
            "name": "AI PR Reviewer",
            "description": description[:140],
            "target_url": target_url
        }
        resp = requests.post(url, headers=self.headers, json=payload)
        return {"success": resp.status_code == 201, "state": gl_state}

    def parse_webhook(self, payload: dict, headers: dict) -> Optional[PRInfo]:
        """Parse GitLab webhook payload for MR events."""
        # Verify token if configured
        if self.webhook_secret=[REDACTED_PASSWORD]
            token = headers.get("X-Gitlab-Token", "")
            if token != self.webhook_secret:
                return None

        event_type = payload.get("object_kind", "")
        if event_type != "merge_request":
            return None

        action = payload.get("object_attributes", {}).get("action", "")
        if action not in ("open", "reopen", "update"):
            return None

        attrs = payload["object_attributes"]
        project = payload.get("project", {})

        # Extract namespace/repo from path_with_namespace
        path = project.get("path_with_namespace", "")
        parts = path.split("/", 1)
        owner = parts[0] if len(parts) > 1 else ""
        repo = parts[1] if len(parts) > 1 else path

        return PRInfo(
            provider="gitlab",
            owner=owner,
            repo=repo,
            pr_number=attrs["iid"],
            branch=attrs["source_branch"],
            base_branch=attrs["target_branch"],
            sha=attrs.get("last_commit", {}).get("id", ""),
            clone_url=project.get("http_url", ""),
            title=attrs.get("title", ""),
            author=payload.get("user", {}).get("username", "")
        )
