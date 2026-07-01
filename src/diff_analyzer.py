"""
Diff Analyzer — Parses PR diffs to identify changed files, added lines,
and determines which security scans are relevant.
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FileDiff:
    """Represents a single file's changes in a PR."""
    filename: str
    status: str                      # added, modified, removed
    language: str = ""               # detected language
    additions: int = 0
    deletions: int = 0
    patch: str = ""
    added_lines: list = field(default_factory=list)  # (line_number, content)
    risk_signals: list = field(default_factory=list)  # detected patterns


@dataclass
class DiffSummary:
    """Summary of all changes in a PR."""
    files: list                      # list of FileDiff
    total_additions: int = 0
    total_deletions: int = 0
    languages: set = field(default_factory=set)
    has_dependency_changes: bool = False
    has_config_changes: bool = False
    has_ci_changes: bool = False
    has_iac_changes: bool = False
    relevant_scans: list = field(default_factory=list)


# File patterns to detect language / type
LANGUAGE_MAP = {
    r"\.py$": "python",
    r"\.js$|\.ts$|\.jsx$|\.tsx$": "javascript",
    r"\.java$": "java",
    r"\.go$": "go",
    r"\.rs$": "rust",
    r"\.rb$": "ruby",
    r"\.php$": "php",
    r"\.cs$": "csharp",
    r"\.c$|\.cpp$|\.h$": "c",
}

IAC_FILES = {
    r"\.tf$": "terraform",
    r"\.tfvars$": "terraform",
    r"template\.yaml$|template\.json$": "cloudformation",
    r"cfn-.*\.(yaml|json)$": "cloudformation",
}

DEPENDENCY_FILES = {
    "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml", "Pipfile",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "pom.xml", "build.gradle", "build.gradle.kts",
    "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock",
    "Gemfile", "Gemfile.lock",
    "composer.json", "composer.lock",
}

CONFIG_FILES = {
    "Dockerfile", "docker-compose.yml", ".env", ".env.example",
    "terraform.tf", "main.tf", "variables.tf",
    "serverless.yml", "template.yaml", "samconfig.toml",
}

CI_FILES = {
    r"\.github/workflows/", r"\.gitlab-ci", r"Jenkinsfile",
    r"\.circleci/", r"\.travis\.yml", r"bitbucket-pipelines",
}

# Risk patterns to flag in added lines
RISK_PATTERNS = [
    (r"(?:password|secret|token|api_key)\s*=\s*['\"][^'\"]+['\"]", "hardcoded_secret"),
    (r"eval\s*\(", "code_injection"),
    (r"exec\s*\(", "code_injection"),
    (r"subprocess\.(call|run|Popen)\s*\(.*shell\s*=\s*True", "shell_injection"),
    (r"os\.system\s*\(", "shell_injection"),
    (r"innerHTML\s*=", "xss_risk"),
    (r"dangerouslySetInnerHTML", "xss_risk"),
    (r"SELECT\s+.*\s+FROM.*\+|f['\"].*SELECT", "sql_injection"),
    (r"pickle\.loads?", "deserialization"),
    (r"yaml\.load\s*\((?!.*Loader)", "deserialization"),
    (r"requests\.get\(.*\{.*\}", "ssrf_risk"),
    (r"chmod\s+777|chmod\s+666", "insecure_permissions"),
    (r"# ?TODO|# ?FIXME|# ?HACK", "tech_debt"),
]


def detect_language(filename: str) -> str:
    """Detect programming language from filename."""
    for pattern, lang in LANGUAGE_MAP.items():
        if re.search(pattern, filename, re.IGNORECASE):
            return lang
    return ""


def analyze_diff(changed_files: list[dict]) -> DiffSummary:
    """
    Analyze PR changed files and produce a structured summary.
    
    Args:
        changed_files: List from provider.get_changed_files()
                       Each has: filename, status, additions, deletions, patch
    
    Returns:
        DiffSummary with categorized files and recommended scans
    """
    files = []
    total_add = 0
    total_del = 0
    languages = set()
    has_deps = False
    has_iac = False
    has_config = False
    has_ci = False

    for f in changed_files:
        filename = f["filename"]
        lang = detect_language(filename)
        if lang:
            languages.add(lang)

        # Check for IaC files
        for iac_pattern, iac_type in IAC_FILES.items():
            if re.search(iac_pattern, filename, re.IGNORECASE):
                has_iac = True
                break

        # Check file categories
        basename = filename.split("/")[-1]
        if basename in DEPENDENCY_FILES:
            has_deps = True
        if basename in CONFIG_FILES:
            has_config = True
        for ci_pattern in CI_FILES:
            if re.search(ci_pattern, filename):
                has_ci = True
                break

        # Parse added lines from patch
        added_lines = []
        risk_signals = []
        patch = f.get("patch", "")

        if patch:
            line_num = 0
            for line in patch.split("\n"):
                # Parse hunk header for line numbers
                hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", line)
                if hunk_match:
                    line_num = int(hunk_match.group(1)) - 1
                    continue

                if line.startswith("+") and not line.startswith("+++"):
                    line_num += 1
                    content = line[1:]
                    added_lines.append((line_num, content))

                    # Check for risk patterns
                    for pattern, risk_type in RISK_PATTERNS:
                        if re.search(pattern, content, re.IGNORECASE):
                            risk_signals.append({
                                "type": risk_type,
                                "line": line_num,
                                "content": content.strip()[:100]
                            })
                elif line.startswith("-") and not line.startswith("---"):
                    pass  # deletion, don't increment line counter
                else:
                    line_num += 1

        file_diff = FileDiff(
            filename=filename,
            status=f.get("status", "modified"),
            language=lang,
            additions=f.get("additions", 0),
            deletions=f.get("deletions", 0),
            patch=patch,
            added_lines=added_lines,
            risk_signals=risk_signals
        )
        files.append(file_diff)
        total_add += file_diff.additions
        total_del += file_diff.deletions

    # Determine relevant scans
    scans = ["security"]  # always run security
    if "python" in languages:
        scans.extend(["bandit", "pip-audit", "ruff"])
    if "javascript" in languages:
        scans.extend(["npm-audit", "eslint-security"])
    if "java" in languages:
        scans.extend(["owasp-depcheck", "spotbugs"])
    if has_deps:
        scans.append("dependency-audit")
    if has_config:
        scans.append("config-audit")

    if has_iac:
        scans.extend(["tfsec", "checkov", "infracost"])
        if any(re.search(r"cfn-|template\.(yaml|json)", f["filename"], re.IGNORECASE) for f in changed_files):
            scans.extend(["cfn-lint", "cfn-nag"])

    return DiffSummary(
        files=files,
        total_additions=total_add,
        total_deletions=total_del,
        languages=languages,
        has_dependency_changes=has_deps,
        has_config_changes=has_config,
        has_ci_changes=has_ci,
        has_iac_changes=has_iac,
        relevant_scans=list(set(scans))
    )


def filter_reviewable_files(files: list[FileDiff], ignore_patterns: list[str] = None) -> list[FileDiff]:
    """Filter out files that should not be reviewed (docs, tests, etc.)."""
    if not ignore_patterns:
        ignore_patterns = []

    default_ignore = [
        r"\.md$", r"\.txt$", r"\.rst$",
        r"LICENSE", r"CHANGELOG",
        r"\.gitignore$", r"\.dockerignore$",
    ]
    patterns = default_ignore + ignore_patterns

    filtered = []
    for f in files:
        skip = False
        for pattern in patterns:
            if re.search(pattern, f.filename, re.IGNORECASE):
                skip = True
                break
        if not skip and f.status != "removed":
            filtered.append(f)

    return filtered
