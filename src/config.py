"""
Configuration loader — reads from YAML file or environment variables.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml


@dataclass
class AIConfig:
    provider: str = "bedrock"
    model: str = "moonshotai.kimi-k2.5"
    temperature: float = 0.2
    max_tokens: int = 4000
    api_key: str = ""


@dataclass
class ReviewConfig:
    post_comment: bool = True
    set_status: bool = True
    fail_on: str = "critical"  # critical | high | medium | never
    ignore_paths: list = field(default_factory=lambda: ["*.md", "docs/*", "*.txt"])
    max_inline_comments: int = 10


@dataclass
class KiroConfig:
    enabled: bool = False
    spec: str = "kiro/review-spec.md"
    depth: str = "standard"  # quick | standard | deep


@dataclass
class Config:
    provider: str = "github"
    ai: AIConfig = field(default_factory=AIConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    kiro: KiroConfig = field(default_factory=KiroConfig)


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Load configuration from YAML file, with env var overrides.
    
    Priority: env vars > config file > defaults
    """
    config = Config()

    # Load from YAML file if it exists
    if config_path and os.path.exists(config_path):
        with open(config_path, "r") as f:
            data = yaml.safe_load(f) or {}

        config.provider = data.get("provider", config.provider)

        if "ai" in data:
            config.ai.provider = data["ai"].get("provider", config.ai.provider)
            config.ai.model = data["ai"].get("model", config.ai.model)
            config.ai.temperature = data["ai"].get("temperature", config.ai.temperature)
            config.ai.max_tokens = data["ai"].get("max_tokens", config.ai.max_tokens)

        if "review" in data:
            config.review.post_comment = data["review"].get("post_comment", config.review.post_comment)
            config.review.set_status = data["review"].get("set_status", config.review.set_status)
            config.review.fail_on = data["review"].get("fail_on", config.review.fail_on)
            config.review.ignore_paths = data["review"].get("ignore_paths", config.review.ignore_paths)
            config.review.max_inline_comments = data["review"].get("max_inline_comments", config.review.max_inline_comments)

        if "kiro" in data:
            config.kiro.enabled = data["kiro"].get("enabled", config.kiro.enabled)
            config.kiro.spec = data["kiro"].get("spec", config.kiro.spec)
            config.kiro.depth = data["kiro"].get("depth", config.kiro.depth)

    # Environment variable overrides
    if os.environ.get("AI_PROVIDER"):
        config.ai.provider = os.environ["AI_PROVIDER"]
    if os.environ.get("AI_MODEL"):
        config.ai.model = os.environ["AI_MODEL"]
    if os.environ.get("FAIL_ON"):
        config.review.fail_on = os.environ["FAIL_ON"]
    if os.environ.get("KIRO_ENABLED"):
        config.kiro.enabled = os.environ["KIRO_ENABLED"].lower() == "true"
    if os.environ.get("OPENAI_API_KEY"):
        config.ai.api_key = os.environ["OPENAI_API_KEY"]

    return config
