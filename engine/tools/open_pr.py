"""``marginalia.open_pr`` — propose changes for review (design §7.4).

Real implementation: branches the wiki repo, commits the staged page
files, pushes, then shells out to ``gh pr create``. Returns a
``PullRequest`` populated with the URL `gh` printed to stdout.

The pattern mirrors ``engine/hooks/dispatcher.py`` for subprocess
discipline — every external command captures stdout/stderr, enforces a
timeout, and surfaces structured failures rather than mystery
exceptions. This is what makes the agent's "open a PR" step debuggable
when things break (auth, branch protection, dirty trees, missing gh).

Design constraint from §7.5 + §13: failures must land in the audit DB
as ``pr_failed`` events when an ``AuditWriter`` is provided, so the
dashboard surfaces them rather than swallowing them in a stack trace.
"""

from __future__ import annotations

import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from engine.audit.writer import AuditWriter

DEFAULT_TIMEOUT_S = 60
DEFAULT_BASE_BRANCH = "main"
DEFAULT_REMOTE = "origin"


class PullRequest(BaseModel):
    """Metadata for an opened pull request."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    body: str
    branch: str = Field(min_length=1)
    files: list[str] = Field(default_factory=list)
    url: str | None = Field(
        default=None,
        description="Populated when the gh backend ran successfully; None on dry-run.",
    )


class OpenPrError(RuntimeError):
    """Raised when the PR cannot be opened.

    Distinct subclasses encode the failure mode so callers can decide
    whether to retry, surface to the user, or skip. Each subclass
    carries the captured stderr from the failing subprocess (when
    applicable) for debuggability.
    """

    def __init__(self, message: str, *, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


class GhMissingError(OpenPrError):
    """`gh` CLI is not on PATH."""


class DirtyWorkingTreeError(OpenPrError):
    """Wiki repo has uncommitted changes outside the staged page files."""


class GitFailure(OpenPrError):
    """A git subprocess returned non-zero."""


class GhFailure(OpenPrError):
    """`gh pr create` returned non-zero (auth, network, branch protection)."""


def open_pr(
    title: str,
    body: str,
    *,
    branch: str,
    files: list[str],
    wiki_root: Path,
    base: str = DEFAULT_BASE_BRANCH,
    remote: str = DEFAULT_REMOTE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    audit_writer: AuditWriter | None = None,
    job_id: str | None = None,
    dry_run: bool = False,
) -> PullRequest:
    """Branch, commit, push, and open a PR for the staged page files.

    Steps:

    1. Sanity-check ``wiki_root`` is a git repo and ``gh`` is on PATH.
    2. Verify ``files`` are the only changes (untracked or modified) —
       a dirty working tree elsewhere is an early refusal so we never
       commit unrelated work the user has in flight.
    3. Switch to a fresh branch (auto-suffix on collision).
    4. ``git add`` the files, commit with the agent's signature.
    5. ``git push -u origin <branch>``.
    6. ``gh pr create --base <base> --head <branch> --title ... --body ...``.
    7. Parse the URL from gh's stdout; return a populated ``PullRequest``.

    On any failure, write an audit event with the captured stderr and
    re-raise the typed exception. ``dry_run=True`` runs no subprocess
    and returns ``url=None`` — useful for tests + the future
    ``--dry-run`` CLI flag.
    """
    pr = PullRequest(title=title, body=body, branch=branch, files=list(files))
    if dry_run:
        return pr

    if not _gh_on_path():
        raise GhMissingError("`gh` CLI not found on PATH; install GitHub CLI to open PRs")
    if not (wiki_root / ".git").exists():
        raise GitFailure(f"{wiki_root} is not a git repository")

    relative_files = [_relativize(f, wiki_root) for f in files]
    _refuse_if_dirty_outside(wiki_root, relative_files, timeout_s=timeout_s)

    final_branch = _branch_with_collision_suffix(wiki_root, branch, timeout_s=timeout_s)
    pr = pr.model_copy(update={"branch": final_branch})

    t0 = time.perf_counter()
    try:
        _git(wiki_root, ["switch", "-c", final_branch], timeout_s=timeout_s)
        _git(wiki_root, ["add", "--", *relative_files], timeout_s=timeout_s)
        _git(
            wiki_root,
            ["commit", "-m", _commit_message(title, body)],
            timeout_s=timeout_s,
        )
        _git(
            wiki_root,
            ["push", "-u", remote, final_branch],
            timeout_s=timeout_s,
        )
        url = _gh_pr_create(
            wiki_root,
            title=title,
            body=body,
            base=base,
            head=final_branch,
            timeout_s=timeout_s,
        )
    except OpenPrError as exc:
        _record_failure(
            audit_writer,
            job_id=job_id,
            branch=final_branch,
            files=relative_files,
            error=type(exc).__name__,
            stderr=exc.stderr,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        raise

    return pr.model_copy(update={"url": url})


# ─── git/gh helpers ──────────────────────────────────────────────────


def _gh_on_path() -> bool:
    """Return True iff `gh --version` runs cleanly. Also satisfied by mocks
    on PATH that respond to `--version`, which is what the tests rely on."""
    try:
        proc = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _git(
    wiki_root: Path,
    argv: list[str],
    *,
    timeout_s: int,
) -> subprocess.CompletedProcess[str]:
    """Run a git command in ``wiki_root``; raise ``GitFailure`` on non-zero."""
    return _run(["git", "-C", str(wiki_root), *argv], timeout_s=timeout_s, exc=GitFailure)


def _gh_pr_create(
    wiki_root: Path,
    *,
    title: str,
    body: str,
    base: str,
    head: str,
    timeout_s: int,
) -> str:
    """Invoke ``gh pr create`` and parse the PR URL from stdout.

    `gh` prints the URL on its own line. Empty stdout means gh ran but
    didn't print a URL — we treat that as a failure rather than
    silently returning ``None`` so the caller knows nothing landed.
    """
    proc = _run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            base,
            "--head",
            head,
            "--title",
            title,
            "--body",
            body,
        ],
        timeout_s=timeout_s,
        exc=GhFailure,
        cwd=wiki_root,
    )
    url = _parse_pr_url(proc.stdout)
    if not url:
        raise GhFailure(
            "gh pr create succeeded but no URL was found in stdout",
            stderr=proc.stdout + "\n" + proc.stderr,
        )
    return url


_GH_URL_RE = re.compile(r"https?://\S*github\.com/\S+/pull/\d+")


def _parse_pr_url(stdout: str) -> str | None:
    match = _GH_URL_RE.search(stdout)
    return match.group(0) if match else None


def _run(
    argv: list[str],
    *,
    timeout_s: int,
    exc: type[OpenPrError],
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, raise ``exc`` with captured stderr on failure."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as fnf:
        raise exc(f"command not found: {argv[0]}", stderr=str(fnf)) from fnf
    except subprocess.TimeoutExpired as to:
        raise exc(
            f"timed out after {timeout_s}s: {' '.join(argv)}",
            stderr=(to.stderr.decode("utf-8", errors="replace") if to.stderr else ""),
        ) from to

    if proc.returncode != 0:
        raise exc(
            f"non-zero exit ({proc.returncode}): {' '.join(argv)}",
            stderr=proc.stderr,
        )
    return proc


_ENGINE_RUNTIME_PREFIX = ".wiki/"


def _refuse_if_dirty_outside(
    wiki_root: Path,
    staged_files: list[str],
    *,
    timeout_s: int,
) -> None:
    """Refuse if any file *other than* the staged set is modified or untracked.

    Uses ``git status --porcelain`` so we get a normalized list. The
    user's in-flight changes shouldn't get swept into the agent's PR
    by accident — that's an irreversible mistake the agent shouldn't
    make autonomously.

    Files under ``.wiki/`` are always ignored: that directory holds the
    engine's runtime state (``audit.db``, ``jobs.db``, cache entries)
    which lives next to the wiki repo for convenience but should never
    enter version control. We don't require users to add it to
    ``.gitignore``; this is the engine's own concern.
    """
    # ``--untracked-files=all`` expands untracked directories into the
    # files they contain, so checks against ``expected`` work whether
    # the staged page lives in a brand-new directory or an existing one.
    proc = _git(
        wiki_root,
        ["status", "--porcelain", "--untracked-files=all"],
        timeout_s=timeout_s,
    )
    expected = set(staged_files)
    dirty: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        path = line[3:]  # status code occupies first two columns + space
        if path in expected:
            continue
        if path.startswith(_ENGINE_RUNTIME_PREFIX):
            continue
        dirty.append(line)
    if dirty:
        raise DirtyWorkingTreeError(
            "wiki repo has uncommitted changes outside the staged files; "
            "commit or stash them first.\n" + "\n".join(dirty)
        )


def _branch_with_collision_suffix(
    wiki_root: Path,
    desired: str,
    *,
    timeout_s: int,
) -> str:
    """If ``desired`` already exists locally or remotely, append a UTC stamp.

    Branch protection rules + remote stale branches make collisions
    common in real wikis. Auto-suffixing keeps the agent unblocked
    without overwriting prior work.
    """
    if not _branch_exists(wiki_root, desired, timeout_s=timeout_s):
        return desired
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{desired}-{stamp}"


def _branch_exists(wiki_root: Path, name: str, *, timeout_s: int) -> bool:
    local = _git(
        wiki_root,
        ["branch", "--list", name],
        timeout_s=timeout_s,
    )
    if local.stdout.strip():
        return True
    # Check remote refs without a network round-trip; relies on the
    # local cache being reasonably fresh from prior fetches.
    remote = _git(
        wiki_root,
        ["branch", "--remotes", "--list", f"*/{name}"],
        timeout_s=timeout_s,
    )
    return bool(remote.stdout.strip())


def _commit_message(title: str, body: str) -> str:
    """Compose a commit message following the repo's footer convention."""
    parts = [title.strip()]
    body = body.strip()
    if body:
        parts.append("")
        parts.append(body)
    parts.append("")
    parts.append("Co-Authored-By: marginalia-engine <noreply@marginalia.local>")
    return "\n".join(parts)


def _relativize(file_str: str, wiki_root: Path) -> str:
    """Normalize a file path to a wiki-root-relative POSIX string for git."""
    p = Path(file_str)
    if p.is_absolute():
        try:
            p = p.relative_to(wiki_root)
        except ValueError as ve:
            raise GitFailure(f"{p} is not inside wiki_root {wiki_root}") from ve
    return p.as_posix()


def _record_failure(
    audit_writer: AuditWriter | None,
    *,
    job_id: str | None,
    branch: str,
    files: list[str],
    error: str,
    stderr: str,
    duration_ms: int,
) -> None:
    if audit_writer is None:
        return
    audit_writer.record_event(
        event_type="pr_failed",
        metadata={
            "branch": branch,
            "files": files,
            "error": error,
            "stderr": stderr[-2000:],
            "duration_ms": duration_ms,
        },
        job_id=job_id,
    )


__all__ = [
    "DEFAULT_BASE_BRANCH",
    "DEFAULT_REMOTE",
    "DEFAULT_TIMEOUT_S",
    "DirtyWorkingTreeError",
    "GhFailure",
    "GhMissingError",
    "GitFailure",
    "OpenPrError",
    "PullRequest",
    "open_pr",
]
