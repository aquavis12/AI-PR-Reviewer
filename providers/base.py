"""
Abstract base class for Git providers (GitHub, GitLab, etc.)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class PRInfo:
    """Normalized pull request / merge request information."""
    provider: str            # "github" or "gitlab"
    owner: str               # repo owner / namespace
    repo: str                # repo name
    pr_number: int           # PR or MR number
    branch: str              # source branch
    base_branch: str         # target branch
    sha: str                 # head commit SHA
    clone_url: str           # HTTPS clone URL
    title: str
    author: str
    diff_url: Optional[str] = None


@dataclass
class ReviewComment:
    """A review comment to post on a PR/MR."""
    body: str
    path: Optional[str] = None      # file path (for inline comments)
    line: Optional[int] = None      # line number (for inline comments)
    side: str = "RIGHT"             # LEFT or RIGHT (for inline comments)


class GitProvider(ABC):
    """Abstract interface for Git hosting providers."""

    @abstractmethod
    def get_pr_info(self, **kwargs) -> PRInfo:
        """Fetch PR/MR metadata from the provider."""
        ...

    @abstractmethod
    def get_pr_diff(self, pr: PRInfo) -> str:
        """Get the unified diff for a PR/MR."""
        ...

    @abstractmethod
    def get_changed_files(self, pr: PRInfo) -> list[dict]:
        """Get list of changed files with their patches."""
        ...

    @abstractmethod
    def post_comment(self, pr: PRInfo, comment: ReviewComment) -> dict:
        """Post a general comment on the PR/MR."""
        ...

    @abstractmethod
    def post_inline_comment(self, pr: PRInfo, comment: ReviewComment) -> dict:
        """Post an inline comment on a specific file/line."""
        ...

    @abstractmethod
    def set_status(self, pr: PRInfo, state: str, description: str, target_url: str = "") -> dict:
        """Set commit status check (success/failure/pending)."""
        ...

    @abstractmethod
    def parse_webhook(self, payload: dict, headers: dict) -> Optional[PRInfo]:
        """Parse incoming webhook payload into PRInfo. Returns None if not a reviewable event."""
        ...
