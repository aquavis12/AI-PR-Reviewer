"""
AI Client — Abstraction layer for AI providers (Bedrock, OpenAI, local).

Handles the AI analysis of PR diffs and scan results.
"""

import json
import os
from typing import Optional

import boto3


class AIClient:
    """Multi-provider AI client for code review analysis."""

    def __init__(
        self,
        provider: str = "bedrock",
        model: str = "moonshotai.kimi-k2.5",
        region: str = "us-east-1",
        temperature: float = 0.2,
        max_tokens: int = 4000,
        api_key: Optional[str] = None,
    ):
        self.provider = provider
        self.model = model
        self.region = region
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

        if provider == "bedrock":
            self.client = boto3.client("bedrock-runtime", region_name=region)

    def review(self, diff_summary: dict, scan_results: dict = None, context: str = "") -> dict:
        """
        Perform AI code review on a PR diff.

        Args:
            diff_summary: Parsed diff (from diff_analyzer)
            scan_results: Optional security scan results
            context: Optional additional context (from Kiro or custom skills)

        Returns:
            Structured review result with findings, risk level, and recommendations
        """
        prompt = self._build_prompt(diff_summary, scan_results, context)

        if self.provider == "bedrock":
            return self._call_bedrock(prompt)
        elif self.provider == "openai":
            return self._call_openai(prompt)
        else:
            raise ValueError(f"Unknown AI provider: {self.provider}")

    def _build_prompt(self, diff_summary: dict, scan_results: dict = None, context: str = "") -> str:
        """Build the review prompt with all context."""

        files_section = ""
        for f in diff_summary.get("files", []):
            if f.get("patch"):
                files_section += f"\n### {f['filename']} ({f.get('status', 'modified')})\n"
                files_section += f"```diff\n{f['patch'][:3000]}\n```\n"

                # Include risk signals
                risks = f.get("risk_signals", [])
                if risks:
                    files_section += "**Detected patterns:**\n"
                    for r in risks:
                        files_section += f"- Line {r['line']}: {r['type']} — `{r['content']}`\n"

        scan_section = ""
        if scan_results:
            scan_section = f"\n## Security Scan Results\n```json\n{json.dumps(scan_results, indent=2)[:4000]}\n```\n"

        context_section = ""
        if context:
            context_section = f"\n## Additional Context\n{context}\n"

        return f"""You are an expert code reviewer specializing in security, code quality, and best practices.

## Task
Review the following pull request changes. Focus on:
1. Security vulnerabilities (OWASP Top 10, supply chain risks)
2. Code quality issues (bugs, logic errors, performance)
3. Best practices violations
4. Potential breaking changes

## PR Changes

**Summary:** {diff_summary.get('total_additions', 0)} additions, {diff_summary.get('total_deletions', 0)} deletions across {len(diff_summary.get('files', []))} files
**Languages:** {', '.join(diff_summary.get('languages', []))}
**Dependency changes:** {'Yes' if diff_summary.get('has_dependency_changes') else 'No'}

{files_section}
{scan_section}
{context_section}

## Required Output Format

Return a JSON object with this exact structure:
{{
  "risk_level": "low|medium|high|critical",
  "summary": "One paragraph summary of findings",
  "safe_to_merge": true|false,
  "confidence": 0.0-1.0,
  "findings": {{
    "security": {{
      "critical": 0,
      "high": 0,
      "medium": 0,
      "low": 0,
      "details": ["finding 1", "finding 2"]
    }},
    "quality": {{
      "score": "A-F",
      "issues": 0,
      "details": ["issue 1"]
    }},
    "breaking_changes": []
  }},
  "inline_comments": [
    {{
      "file": "path/to/file.py",
      "line": 42,
      "severity": "high",
      "comment": "Explain the issue and fix"
    }}
  ],
  "recommendations": [
    {{
      "action": "What to do",
      "priority": "critical|high|medium|low",
      "effort": "trivial|small|medium|large"
    }}
  ]
}}

Be specific. Reference exact line numbers. Don't flag style preferences — only real issues."""

    def _call_bedrock(self, prompt: str) -> dict:
        """Call AWS Bedrock Converse API."""
        system_prompt = (
            "You are a senior security engineer and code reviewer. "
            "Analyze code changes for vulnerabilities, bugs, and quality issues. "
            "Return structured JSON. Be precise and cite specific lines."
        )

        response = self.client.converse(
            modelId=self.model,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={
                "temperature": self.temperature,
                "maxTokens": self.max_tokens,
            }
        )

        # Extract text from response
        output_text = ""
        for block in response["output"]["message"]["content"]:
            if "text" in block:
                output_text += block["text"]

        # Parse JSON from response
        return self._parse_response(output_text)

    def _call_openai(self, prompt: str) -> dict:
        """Call OpenAI API (for non-AWS deployments)."""
        import requests

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a senior security engineer and code reviewer. Return structured JSON."},
                {"role": "user", "content": prompt}
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }

        resp = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()

        output_text = resp.json()["choices"][0]["message"]["content"]
        return self._parse_response(output_text)

    def _parse_response(self, text: str) -> dict:
        """Parse AI response — extract JSON from potentially wrapped text."""
        # Try direct JSON parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code block
        import re
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Fallback — return as summary
        return {
            "risk_level": "unknown",
            "summary": text[:500],
            "safe_to_merge": True,
            "confidence": 0.5,
            "findings": {"security": {"critical": 0, "high": 0, "medium": 0, "low": 0, "details": []}},
            "inline_comments": [],
            "recommendations": []
        }
