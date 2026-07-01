"""
IaC Scanner — Terraform and CloudFormation security + cost scanning.

Tools:
- tfsec: Terraform static analysis
- checkov: Multi-framework policy scanner
- cfn-lint: CloudFormation linter
- cfn-nag: CloudFormation security scanner
- infracost: Cloud cost estimation from Terraform

Branch: feature/infra-scanning
"""

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class IaCFinding:
    """A single IaC scan finding."""
    tool: str
    severity: str         # critical, high, medium, low, info
    rule_id: str
    message: str
    file: str
    line: int = 0
    resource: str = ""
    fix_suggestion: str = ""


@dataclass
class CostEstimate:
    """Cost estimation for a resource."""
    resource_name: str
    resource_type: str
    monthly_cost: float
    hourly_cost: float = 0.0
    currency: str = "USD"


@dataclass
class IaCScanResult:
    """Complete IaC scan results."""
    findings: list = field(default_factory=list)       # List of IaCFinding
    cost_estimates: list = field(default_factory=list)  # List of CostEstimate
    total_monthly_cost: float = 0.0
    cost_diff: float = 0.0  # Change from base branch
    tools_run: list = field(default_factory=list)
    errors: list = field(default_factory=list)


class TerraformScanner:
    """Scan Terraform files with tfsec and checkov."""

    def scan(self, working_dir: str) -> list[IaCFinding]:
        """Run tfsec and checkov on Terraform files."""
        findings = []
        findings.extend(self._run_tfsec(working_dir))
        findings.extend(self._run_checkov(working_dir, framework="terraform"))
        return findings

    def _run_tfsec(self, working_dir: str) -> list[IaCFinding]:
        """Run tfsec static analysis."""
        findings = []
        try:
            result = subprocess.run(
                ["tfsec", working_dir, "--format", "json", "--no-color"],
                capture_output=True, text=True, timeout=120
            )
            data = json.loads(result.stdout) if result.stdout else {}

            for finding in data.get("results", []):
                severity = finding.get("severity", "MEDIUM").lower()
                findings.append(IaCFinding(
                    tool="tfsec",
                    severity=severity,
                    rule_id=finding.get("rule_id", ""),
                    message=finding.get("description", ""),
                    file=finding.get("location", {}).get("filename", ""),
                    line=finding.get("location", {}).get("start_line", 0),
                    resource=finding.get("resource", ""),
                    fix_suggestion=finding.get("resolution", ""),
                ))
        except FileNotFoundError:
            pass  # tfsec not installed
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            pass

        return findings

    def _run_checkov(self, working_dir: str, framework: str = "terraform") -> list[IaCFinding]:
        """Run checkov policy scanner."""
        findings = []
        try:
            result = subprocess.run(
                ["checkov", "-d", working_dir, "--framework", framework,
                 "--output", "json", "--quiet", "--compact"],
                capture_output=True, text=True, timeout=180
            )
            data = json.loads(result.stdout) if result.stdout else {}

            for check_result in data.get("results", {}).get("failed_checks", []):
                # Map checkov severity
                severity = check_result.get("severity", "MEDIUM")
                if severity == "CRITICAL":
                    severity = "critical"
                elif severity == "HIGH":
                    severity = "high"
                else:
                    severity = "medium"

                findings.append(IaCFinding(
                    tool="checkov",
                    severity=severity,
                    rule_id=check_result.get("check_id", ""),
                    message=check_result.get("check_name", ""),
                    file=check_result.get("file_path", ""),
                    line=check_result.get("file_line_range", [0])[0],
                    resource=check_result.get("resource", ""),
                    fix_suggestion=check_result.get("guideline", ""),
                ))
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass

        return findings


class CloudFormationScanner:
    """Scan CloudFormation templates with cfn-lint and cfn-nag."""

    def scan(self, template_path: str) -> list[IaCFinding]:
        """Run cfn-lint and cfn-nag on a CloudFormation template."""
        findings = []
        findings.extend(self._run_cfn_lint(template_path))
        findings.extend(self._run_cfn_nag(template_path))
        return findings

    def _run_cfn_lint(self, template_path: str) -> list[IaCFinding]:
        """Run CloudFormation linter."""
        findings = []
        try:
            result = subprocess.run(
                ["cfn-lint", template_path, "--format", "json"],
                capture_output=True, text=True, timeout=60
            )
            data = json.loads(result.stdout) if result.stdout else []

            for issue in data:
                # cfn-lint levels: Error, Warning, Informational
                level = issue.get("Level", "Warning")
                severity = "high" if level == "Error" else "medium" if level == "Warning" else "low"

                findings.append(IaCFinding(
                    tool="cfn-lint",
                    severity=severity,
                    rule_id=issue.get("Rule", {}).get("Id", ""),
                    message=issue.get("Message", ""),
                    file=issue.get("Filename", template_path),
                    line=issue.get("Location", {}).get("Start", {}).get("LineNumber", 0),
                    resource=issue.get("Rule", {}).get("ShortDescription", ""),
                ))
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass

        return findings

    def _run_cfn_nag(self, template_path: str) -> list[IaCFinding]:
        """Run cfn-nag security scanner."""
        findings = []
        try:
            result = subprocess.run(
                ["cfn_nag_scan", "--input-path", template_path, "--output-format", "json"],
                capture_output=True, text=True, timeout=60
            )
            data = json.loads(result.stdout) if result.stdout else []

            for file_result in data:
                for violation in file_result.get("file_results", {}).get("violations", []):
                    severity = "high" if violation.get("type") == "FAIL" else "medium"
                    findings.append(IaCFinding(
                        tool="cfn-nag",
                        severity=severity,
                        rule_id=violation.get("id", ""),
                        message=violation.get("message", ""),
                        file=template_path,
                        line=violation.get("line_numbers", [0])[0] if violation.get("line_numbers") else 0,
                        resource=", ".join(violation.get("logical_resource_ids", [])),
                    ))
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass

        return findings


class CostEstimator:
    """Estimate cloud costs using Infracost."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.environ.get("INFRACOST_API_KEY", "")

    def estimate(self, working_dir: str, base_dir: Optional[str] = None) -> dict:
        """
        Run infracost on Terraform directory.

        Args:
            working_dir: Path to Terraform files (PR branch)
            base_dir: Path to Terraform files (base branch, for diff)

        Returns:
            Cost breakdown with monthly estimates and diff
        """
        result = {
            "resources": [],
            "total_monthly_cost": 0.0,
            "cost_diff": 0.0,
            "currency": "USD"
        }

        if not self.api_key:
            result["error"] = "INFRACOST_API_KEY not set — cost estimation skipped"
            return result

        try:
            # Run infracost breakdown
            cmd = [
                "infracost", "breakdown",
                "--path", working_dir,
                "--format", "json",
                "--no-color"
            ]

            env = os.environ.copy()
            env["INFRACOST_API_KEY"] = self.api_key

            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
            data = json.loads(proc.stdout) if proc.stdout else {}

            # Parse resources
            for project in data.get("projects", []):
                for resource in project.get("breakdown", {}).get("resources", []):
                    monthly = resource.get("monthlyCost")
                    if monthly:
                        result["resources"].append({
                            "name": resource.get("name", ""),
                            "type": resource.get("resourceType", ""),
                            "monthly_cost": float(monthly),
                        })
                        result["total_monthly_cost"] += float(monthly)

            # Run diff if base provided
            if base_dir:
                diff_result = self._run_diff(working_dir, base_dir, env)
                result["cost_diff"] = diff_result.get("diff", 0.0)
                result["diff_details"] = diff_result.get("details", [])

        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            result["error"] = f"Infracost failed: {str(e)}"

        return result

    def _run_diff(self, current_dir: str, base_dir: str, env: dict) -> dict:
        """Run infracost diff between base and PR branch."""
        try:
            cmd = [
                "infracost", "diff",
                "--path", current_dir,
                "--compare-to", base_dir,
                "--format", "json",
                "--no-color"
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
            data = json.loads(proc.stdout) if proc.stdout else {}

            diff_monthly = data.get("diffTotalMonthlyCost", "0")
            return {"diff": float(diff_monthly), "details": data.get("projects", [])}
        except Exception:
            return {"diff": 0.0}


def scan_iac(working_dir: str, file_types: list[str] = None) -> IaCScanResult:
    """
    Main entry point — scans IaC files in a directory.

    Args:
        working_dir: Path containing IaC files
        file_types: List of detected types ("terraform", "cloudformation")

    Returns:
        IaCScanResult with findings and cost estimates
    """
    if file_types is None:
        file_types = detect_iac_types(working_dir)

    result = IaCScanResult()

    if "terraform" in file_types:
        tf_scanner = TerraformScanner()
        result.findings.extend(tf_scanner.scan(working_dir))
        result.tools_run.extend(["tfsec", "checkov"])

        # Cost estimation
        estimator = CostEstimator()
        cost = estimator.estimate(working_dir)
        result.total_monthly_cost = cost.get("total_monthly_cost", 0.0)
        result.cost_diff = cost.get("cost_diff", 0.0)
        for r in cost.get("resources", []):
            result.cost_estimates.append(CostEstimate(
                resource_name=r["name"],
                resource_type=r["type"],
                monthly_cost=r["monthly_cost"]
            ))
        result.tools_run.append("infracost")

    if "cloudformation" in file_types:
        cfn_scanner = CloudFormationScanner()
        # Find CFN templates
        for path in Path(working_dir).rglob("*.yaml"):
            if _is_cfn_template(path):
                result.findings.extend(cfn_scanner.scan(str(path)))
        for path in Path(working_dir).rglob("*.json"):
            if _is_cfn_template(path):
                result.findings.extend(cfn_scanner.scan(str(path)))
        result.tools_run.extend(["cfn-lint", "cfn-nag"])

    return result


def detect_iac_types(directory: str) -> list[str]:
    """Detect which IaC frameworks are present in a directory."""
    types = []
    dir_path = Path(directory)

    if list(dir_path.rglob("*.tf")):
        types.append("terraform")
    if any(_is_cfn_template(p) for p in dir_path.rglob("*.yaml")):
        types.append("cloudformation")
    if any(_is_cfn_template(p) for p in dir_path.rglob("*.json")):
        types.append("cloudformation")

    return list(set(types))


def _is_cfn_template(path: Path) -> bool:
    """Check if a YAML/JSON file is a CloudFormation template."""
    try:
        content = path.read_text(encoding="utf-8")[:500]
        return "AWSTemplateFormatVersion" in content or "aws-cdk" in content
    except Exception:
        return False
