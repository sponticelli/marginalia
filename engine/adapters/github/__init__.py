"""GitHub source adapter — issues / PRs / discussions / wiki via the `gh` CLI."""

from engine.adapters.github.extractor import (
    GitHubExtractError,
    GitHubRef,
    extract_github,
    parse_github_url,
)

__all__ = [
    "GitHubExtractError",
    "GitHubRef",
    "extract_github",
    "parse_github_url",
]
