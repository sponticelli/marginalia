"""GitHub adapter — fetch an issue / PR / discussion via the `gh` CLI.

Why ``gh`` instead of ``pygithub`` or raw REST: ``gh`` is already on
PATH (Phase 1's ``open_pr`` requires it) and ``gh auth`` already
covers user authentication. Using the same binary means no second
auth dance, no separate token to manage. Cost: we shell out per
call. Benefit: zero-maintenance auth + repo-scoped permissions
inherit from whatever the user already configured for `gh`.

The adapter dispatches on URL form:

- ``github.com/<owner>/<repo>/issues/<n>``       → issue + comments
- ``github.com/<owner>/<repo>/pull/<n>``         → PR + reviews + comments
- ``github.com/<owner>/<repo>/discussions/<n>``  → discussion + comments
- ``gh:<owner>/<repo>#<n>`` shorthand            → assumed issue/PR; gh auto-routes

Wiki pages (``github.com/<owner>/<repo>/wiki/<title>``) are deferred:
the wiki repos are separate git repos and best ingested via
``local_fs`` after a ``git clone``.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Literal

from engine.adapters._template.contract import ExtractedContent

DEFAULT_TIMEOUT_S = 30
GitHubKind = Literal["issue", "pr", "discussion"]

# Full-URL form: github.com/<owner>/<repo>/{issues|pull|discussions}/<n>
_URL_RE = re.compile(
    r"github\.com/([^/]+)/([^/]+)/(issues|pull|discussions)/(\d+)",
)
# Shorthand: gh:<owner>/<repo>#<n>  — assume issue/PR, defer to gh's auto-routing.
_SHORTHAND_RE = re.compile(
    r"^gh:([^/]+)/([^#]+)#(\d+)$",
)


class GitHubExtractError(ValueError):
    """Raised when input isn't a recognizable GitHub URL or shorthand."""


@dataclass(frozen=True)
class GitHubRef:
    """Resolved (owner, repo, kind, number) tuple from a URL or shorthand."""

    owner: str
    repo: str
    kind: GitHubKind
    number: int


def parse_github_url(input_str: str) -> GitHubRef:
    """Parse a GitHub URL or ``gh:owner/repo#N`` shorthand into a ``GitHubRef``."""
    short = _SHORTHAND_RE.match(input_str.strip())
    if short:
        # Shorthand defaults to issue; gh's `issue view` covers PRs too via
        # the same ID space, but we mark it `issue` so the rendering header
        # is accurate. Users wanting PR-specific output use the full URL.
        return GitHubRef(
            owner=short.group(1),
            repo=short.group(2),
            kind="issue",
            number=int(short.group(3)),
        )

    match = _URL_RE.search(input_str)
    if match:
        owner, repo, path_kind, number = match.groups()
        kind: GitHubKind
        if path_kind == "pull":
            kind = "pr"
        elif path_kind == "discussions":
            kind = "discussion"
        else:
            kind = "issue"
        return GitHubRef(owner=owner, repo=repo, kind=kind, number=int(number))

    raise GitHubExtractError(
        f"could not parse GitHub URL or shorthand from {input_str!r}; "
        "expected 'github.com/<owner>/<repo>/{issues|pull|discussions}/<n>' "
        "or 'gh:<owner>/<repo>#<n>'"
    )


# ─── gh CLI plumbing ────────────────────────────────────────────────


def _run_gh(argv: list[str], *, timeout_s: int) -> str:
    """Run ``gh <argv>`` and return stdout. Raises on non-zero exit."""
    proc = subprocess.run(
        ["gh", *argv],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh {argv[0]} failed (exit {proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def _gh_view_argv(ref: GitHubRef) -> list[str]:
    """Compose the right ``gh ... view --json ... fields`` invocation per kind."""
    repo = f"{ref.owner}/{ref.repo}"
    if ref.kind == "issue":
        return [
            "issue",
            "view",
            str(ref.number),
            "--repo",
            repo,
            "--json",
            "number,title,body,author,createdAt,updatedAt,state,labels,comments",
        ]
    if ref.kind == "pr":
        return [
            "pr",
            "view",
            str(ref.number),
            "--repo",
            repo,
            "--json",
            "number,title,body,author,createdAt,updatedAt,state,labels,comments,reviews",
        ]
    # discussion
    return [
        "api",
        f"repos/{repo}/discussions/{ref.number}",
    ]


def _fetch_via_gh(ref: GitHubRef, *, timeout_s: int) -> dict:
    """Run ``gh`` for the right entity and parse its JSON output."""
    out = _run_gh(_gh_view_argv(ref), timeout_s=timeout_s)
    return json.loads(out) if out.strip() else {}


# ─── rendering ──────────────────────────────────────────────────────


def _author_handle(author: dict | str | None) -> str:
    """``gh`` returns author as a dict with ``login``; api returns a string."""
    if isinstance(author, dict):
        return author.get("login") or "(unknown)"
    return str(author or "(unknown)")


def _format_comment(comment: dict, *, kind: str = "comment") -> str:
    """Render one comment as a markdown block. ``kind`` distinguishes review vs comment."""
    author = _author_handle(comment.get("author") or comment.get("user"))
    created = (
        comment.get("createdAt")
        or comment.get("created_at")
        or comment.get("submittedAt")
        or "(no date)"
    )
    body = (comment.get("body") or comment.get("body_text") or "").strip()
    head = f"### {kind} by @{author} — {created}"
    state = comment.get("state")
    if state:
        head += f" *(state: {state})*"
    return f"{head}\n\n{body or '(empty)'}\n"


def _format_entity(ref: GitHubRef, data: dict) -> str:
    """Build the full markdown body for one issue / PR / discussion."""
    title = data.get("title", "(no title)")
    author = _author_handle(data.get("author") or data.get("user"))
    created = data.get("createdAt") or data.get("created_at") or "(no date)"
    state = data.get("state", "?")
    labels = data.get("labels") or []
    label_names = [(lbl.get("name") if isinstance(lbl, dict) else str(lbl)) for lbl in labels]
    body = (data.get("body") or data.get("body_text") or "").strip()

    parts: list[str] = [
        f"# {ref.kind.upper()} #{ref.number}: {title}",
        "",
        f"Source: github.com/{ref.owner}/{ref.repo}/{ref.kind}/{ref.number}",
        f"Author: @{author}",
        f"Created: {created}",
        f"State: {state}",
    ]
    if label_names:
        parts.append(f"Labels: {', '.join(label_names)}")
    parts.append("")
    parts.append("## Body")
    parts.append("")
    parts.append(body or "(empty body)")

    comments = data.get("comments") or []
    if comments:
        parts.append("")
        parts.append(f"## Comments ({len(comments)})")
        parts.append("")
        for c in comments:
            parts.append(_format_comment(c, kind="comment"))

    reviews = data.get("reviews") or []
    if reviews:
        parts.append(f"## Reviews ({len(reviews)})")
        parts.append("")
        for r in reviews:
            parts.append(_format_comment(r, kind="review"))

    return "\n".join(parts)


def _failure(reason: str) -> ExtractedContent:
    return ExtractedContent(
        text="",
        pages=None,
        extraction_method="text",
        failure_reason=reason,
    )


# ─── public entry ───────────────────────────────────────────────────


async def extract_github(
    input_str: str,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    fetcher=None,
) -> ExtractedContent:
    """Fetch a GitHub issue / PR / discussion → markdown ``ExtractedContent``.

    ``fetcher`` is a hook for tests: a callable ``(ref) -> dict`` that
    returns the parsed ``gh`` payload directly, bypassing the
    subprocess. Production callers leave it ``None`` and the adapter
    shells out to ``gh``.
    """
    try:
        ref = parse_github_url(input_str)
    except GitHubExtractError as exc:
        return _failure(f"invalid_url: {exc}")

    try:
        if fetcher is not None:
            data = fetcher(ref)
        else:
            data = await asyncio.to_thread(_fetch_via_gh, ref, timeout_s=timeout_s)
    except FileNotFoundError:
        return _failure("gh_missing: gh CLI not found on PATH")
    except subprocess.TimeoutExpired:
        return _failure(f"timeout: gh exceeded {timeout_s}s")
    except json.JSONDecodeError as exc:
        return _failure(f"parse_failed: gh stdout was not JSON ({exc})")
    except RuntimeError as exc:
        return _failure(f"gh_failed: {exc}")

    if not data:
        return _failure("empty_response")

    body = _format_entity(ref, data)
    return ExtractedContent(
        text=body,
        pages=None,
        extraction_method="text",
        cost_usd=None,
        failure_reason=None,
    )


__all__ = [
    "GitHubExtractError",
    "GitHubKind",
    "GitHubRef",
    "extract_github",
    "parse_github_url",
]
