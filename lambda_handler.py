"""
AWS Lambda Handler — Receives webhooks from GitHub/GitLab and triggers reviews.

Flow:
1. Webhook arrives (PR opened/updated)
2. Auto-detect provider (GitHub/GitLab) from headers
3. Parse PR info
4. Spawn MicroVM → clone repo → read .ai-review/ context → run scans → destroy
5. AI analysis (Bedrock Kimi K2.5) with context
6. Post review comment + set commit status

Deploy as Lambda Function URL for direct webhook integration.
"""

import json
import os
import base64

import boto3

from src.config import load_config
from src.ai_client import AIClient
from src.reviewer import PRReviewer
from providers import create_provider


REGION = os.environ.get("AWS_REGION", "us-east-1")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.yaml")
GITHUB_TOKEN_SECRET_ARN = os.environ.get("GITHUB_TOKEN_SECRET_ARN", "")
GITLAB_TOKEN_SECRET_ARN = os.environ.get("GITLAB_TOKEN_SECRET_ARN", "")


def _get_secret(arn: str) -> str:
    """Retrieve a secret value from Secrets Manager."""
    if not arn:
        return ""
    client = boto3.client("secretsmanager", region_name=REGION)
    resp = client.get_secret_value(SecretId=arn)
    value = resp["SecretString"]
    try:
        data = json.loads(value)
        return data.get("token") or data.get("GITHUB_TOKEN") or data.get("GITLAB_TOKEN") or value
    except (json.JSONDecodeError, TypeError):
        return value


def handler(event, context):
    """Lambda Function URL entry point."""
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")

    # Health check
    if method == "GET" and path in ("/", "/health"):
        return response(200, {"status": "healthy", "service": "AI PR Reviewer"})

    # Only accept POST
    if method != "POST":
        return response(405, {"error": "Method not allowed"})

    # Parse body
    raw_body = event.get("body", "{}")
    if event.get("isBase64Encoded", False):
        raw_body = base64.b64decode(raw_body).decode("utf-8")

    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        return response(400, {"error": "Invalid JSON body"})

    # Load config
    config = load_config(CONFIG_PATH)

    # Detect provider from headers or body
    headers = event.get("headers", {})
    provider_name = _detect_provider(headers, body)

    if not provider_name:
        return response(400, {"error": "Cannot determine provider. Use GitHub or GitLab webhook format, or pass 'provider' field."})

    # Get token
    if provider_name == "github":
        token = os.environ.get("GITHUB_TOKEN", "") or _get_secret(GITHUB_TOKEN_SECRET_ARN)
    else:
        token = os.environ.get("GITLAB_TOKEN", "") or _get_secret(GITLAB_TOKEN_SECRET_ARN)

    if not token:
        return response(500, {"error": f"No token configured for {provider_name}"})

    # Create provider
    provider_kwargs = {"token": token}
    if provider_name == "gitlab":
        provider_kwargs["base_url"] = os.environ.get("GITLAB_URL", "https://gitlab.com")

    provider = create_provider(provider_name, **provider_kwargs)

    # Parse webhook → PRInfo
    pr_info = provider.parse_webhook(body, headers)

    # If not a webhook format, try direct call format
    if not pr_info:
        owner = body.get("owner", "")
        repo = body.get("repo", "")
        pr_number = body.get("pr_number", 0)

        if owner and repo and pr_number:
            pr_info = provider.get_pr_info(owner=owner, repo=repo, pr_number=int(pr_number))
        else:
            return response(200, {"message": "Event ignored (not a reviewable PR event)"})

    # Create AI client
    ai_client = AIClient(
        provider=config.ai.provider,
        model=config.ai.model,
        region=REGION,
        temperature=config.ai.temperature,
        max_tokens=config.ai.max_tokens,
        api_key=config.ai.api_key
    )

    # Run the review
    reviewer = PRReviewer(config=config, provider=provider, ai_client=ai_client)

    try:
        result = reviewer.review_pr(pr_info)
    except Exception as e:
        # Set failure status
        try:
            provider.set_status(pr_info, "error", f"Review failed: {str(e)[:100]}")
        except Exception:
            pass
        return response(500, {"error": f"Review failed: {str(e)}", "pr": f"{pr_info.owner}/{pr_info.repo}#{pr_info.pr_number}"})

    return response(200, result)


def _detect_provider(headers: dict, body: dict) -> str:
    """Detect whether the webhook is from GitHub or GitLab."""
    if headers.get("x-github-event") or headers.get("X-GitHub-Event"):
        return "github"
    if headers.get("x-gitlab-event") or headers.get("X-Gitlab-Event"):
        return "gitlab"
    if "pull_request" in body:
        return "github"
    if "object_kind" in body and body["object_kind"] == "merge_request":
        return "gitlab"
    return body.get("provider", "")


def response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str)
    }
