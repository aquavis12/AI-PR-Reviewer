"""
Context Manager — Loads context files from the CLONED REPO inside the MicroVM.

Context files live in the user's own repo under `.ai-review/` (like .github/ or .eslintrc).
When the MicroVM clones the repo for scanning, this module reads the context files
from the clone and injects them into the AI prompt.

NOT pre-configured — users create their own `.ai-review/` directory with whatever
rules, standards, and documentation they want the AI to know about.

## How it works:
1. MicroVM clones the PR branch
2. Context manager checks for `.ai-review/` in the repo root
3. Loads files in order: global.md → category/ → project-specific
4. Concatenated context is injected into the AI review prompt

## User's repo structure:
```
their-repo/
├── .ai-review/                    # Context for AI PR Reviewer
│   ├── context.md                 # Main context (what the project does, standards)
│   ├── security.md                # Security-specific rules
│   ├── cost-policy.md             # Cost thresholds (for IaC PRs)
│   └── terraform/
│       └── standards.md           # Terraform-specific rules
├── src/
│   └── ...
└── README.md
```
"""

import os
from pathlib import Path
from typing import Optional


# The directory name users put in their repos
CONTEXT_DIR_NAME = ".ai-review"


class ContextManager:
    """Loads context files from a cloned repository."""

    def __init__(self, repo_root: str):
        """
        Args:
            repo_root: Path to the cloned repository root (inside MicroVM)
        """
        self.repo_root = Path(repo_root)
        self.context_dir = self.repo_root / CONTEXT_DIR_NAME

    def has_context(self) -> bool:
        """Check if the repo has a .ai-review/ directory."""
        return self.context_dir.is_dir()

    def get_context(
        self,
        category: Optional[str] = None,
        languages: Optional[list] = None,
        has_iac: bool = False,
    ) -> str:
        """
        Load and concatenate relevant context files from the repo.

        Args:
            category: Scan category (e.g., "terraform", "cloudformation")
            languages: Detected languages in the PR
            has_iac: Whether the PR contains IaC changes

        Returns:
            Concatenated context string to inject into AI prompt.
            Empty string if no .ai-review/ directory exists.
        """
        if not self.has_context():
            return ""

        sections = []

        # 1. Main context file (context.md or global.md)
        main_ctx = self._load_file("context.md") or self._load_file("global.md")
        if main_ctx:
            sections.append(main_ctx)

        # 2. Security context
        security_ctx = self._load_file("security.md")
        if security_ctx:
            sections.append(security_ctx)

        # 3. Category-specific context (e.g., terraform/, cloudformation/)
        if category:
            cat_ctx = self._load_directory(category)
            if cat_ctx:
                sections.append(cat_ctx)

        # 4. Language-specific context
        if languages:
            for lang in languages:
                lang_ctx = self._load_file(f"{lang}.md")
                if lang_ctx:
                    sections.append(lang_ctx)

        # 5. IaC-specific context
        if has_iac:
            tf_ctx = self._load_directory("terraform")
            if tf_ctx:
                sections.append(tf_ctx)
            cfn_ctx = self._load_directory("cloudformation")
            if cfn_ctx:
                sections.append(cfn_ctx)
            cost_ctx = self._load_file("cost-policy.md")
            if cost_ctx:
                sections.append(cost_ctx)

        # 6. Load ALL .md files at root level that we haven't already loaded
        known = {"context.md", "global.md", "security.md", "cost-policy.md"}
        if languages:
            known.update(f"{l}.md" for l in languages)
        for md_file in sorted(self.context_dir.glob("*.md")):
            if md_file.name not in known:
                content = md_file.read_text(encoding="utf-8").strip()
                if content:
                    sections.append(content)

        return "\n\n---\n\n".join(sections) if sections else ""

    def list_context_files(self) -> list[dict]:
        """List all context files found in the repo's .ai-review/ directory."""
        if not self.has_context():
            return []

        files = []
        for path in sorted(self.context_dir.rglob("*.md")):
            rel_path = path.relative_to(self.context_dir)
            files.append({
                "name": str(rel_path).replace("\\", "/"),
                "size": path.stat().st_size,
            })
        return files

    def _load_file(self, name: str) -> str:
        """Load a single context file."""
        path = self.context_dir / name
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8").strip()
        return ""

    def _load_directory(self, subdir: str) -> str:
        """Load all .md files from a context subdirectory."""
        dir_path = self.context_dir / subdir
        if not dir_path.is_dir():
            return ""

        parts = []
        for path in sorted(dir_path.glob("*.md")):
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)

        return "\n\n".join(parts)
