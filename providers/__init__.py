"""Git provider factory."""

from providers.base import GitProvider, PRInfo, ReviewComment
from providers.github import GitHubProvider
from providers.gitlab import GitLabProvider


def create_provider(provider_name: str, **kwargs) -> GitProvider:
    """Factory function to create the appropriate Git provider."""
    providers = {
        "github": GitHubProvider,
        "gitlab": GitLabProvider,
    }

    if provider_name not in providers:
        raise ValueError(f"Unknown provider: {provider_name}. Supported: {list(providers.keys())}")

    return providers[provider_name](**kwargs)


__all__ = [
    "GitProvider",
    "PRInfo",
    "ReviewComment",
    "GitHubProvider",
    "GitLabProvider",
    "create_provider",
]
