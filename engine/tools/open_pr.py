"""``marginalia.open_pr`` — propose changes for review (design §7.4).

Print-stub for the PoC: returns a structured ``PullRequest`` object
describing what would be opened. Real GitHub integration (gh CLI,
branch management, auth) lands when the CLI surface gets fleshed out.
The function signature is stable, so callers don't change.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PullRequest(BaseModel):
    """Metadata for a would-be / actual pull request."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    body: str
    branch: str = Field(min_length=1)
    files: list[str] = Field(default_factory=list)
    url: str | None = Field(
        default=None,
        description="Populated only when a real backend (gh, GitHub API) ran.",
    )


def open_pr(
    title: str,
    body: str,
    *,
    branch: str,
    files: list[str],
) -> PullRequest:
    """Return a ``PullRequest`` describing the proposed change.

    Today this is a metadata-only stub — no branch is pushed, no PR
    actually opens on GitHub. Future work wires this to ``gh pr create``
    or ``pygithub``; the return type already carries the ``url`` field
    that real impls populate.
    """
    return PullRequest(title=title, body=body, branch=branch, files=list(files), url=None)


__all__ = ["PullRequest", "open_pr"]
