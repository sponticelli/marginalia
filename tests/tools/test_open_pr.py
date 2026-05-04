"""Tests for ``marginalia.open_pr`` — the real git+gh implementation.

Strategy: spin up a temp git repo + bare remote, mock ``gh`` via a
shell script on PATH. No network, no real GitHub API calls.

Each test that needs a working ``gh`` builds the mock with
``_make_gh_mock(tmp_path, behavior)``; tests for the "gh missing" case
set PATH to a directory that doesn't contain ``gh``.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from engine.tools.open_pr import (
    DirtyWorkingTreeError,
    GhFailure,
    GhMissingError,
    open_pr,
)

# ─── helpers ────────────────────────────────────────────────────────


def _git(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    """Run a git command against ``repo`` (real git, not mocked)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *argv],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc


def _make_wiki_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Create a working wiki repo + bare remote + initial commit on main."""
    bare = tmp_path / "wiki-remote.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(bare)], check=True)

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git(wiki, "init", "--initial-branch=main")
    _git(wiki, "config", "user.email", "test@example.com")
    _git(wiki, "config", "user.name", "Test")
    (wiki / "README.md").write_text("# wiki\n")
    _git(wiki, "add", "README.md")
    _git(wiki, "commit", "-m", "initial")
    _git(wiki, "remote", "add", "origin", str(bare))
    _git(wiki, "push", "-u", "origin", "main")
    return wiki, bare


def _make_gh_mock(
    tmp_path: Path,
    *,
    behavior: str = "ok",
    pr_url: str = "https://github.com/example/wiki/pull/42",
) -> Path:
    """Write an executable ``gh`` shell stub into ``tmp_path/bin``.

    Behaviors:
    - ``"ok"`` — print ``pr_url`` on ``pr create``; exit 0 on ``--version``.
    - ``"fail_pr"`` — exit 1 on ``pr create`` with stderr noise.
    - ``"empty_url"`` — exit 0 on ``pr create`` but no URL in stdout.
    - ``"missing_version"`` — exit 1 on ``--version`` (simulates broken install).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh_path = bin_dir / "gh"

    if behavior == "missing_version":
        script = "#!/bin/sh\nexit 1\n"
    elif behavior == "fail_pr":
        script = (
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "gh version 2.0.0"; exit 0; fi\n'
            'echo "auth required" >&2\n'
            "exit 1\n"
        )
    elif behavior == "empty_url":
        script = (
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "gh version 2.0.0"; exit 0; fi\n'
            'echo "creating pr..."\n'
            "exit 0\n"
        )
    else:  # ok
        script = (
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "gh version 2.0.0"; exit 0; fi\n'
            f'echo "{pr_url}"\n'
            "exit 0\n"
        )

    gh_path.write_text(script)
    gh_path.chmod(gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


@pytest.fixture
def path_with_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Yields a function that prepends a configured gh-mock dir to PATH."""

    def _set(behavior: str = "ok", pr_url: str = "https://github.com/example/wiki/pull/42"):
        bin_dir = _make_gh_mock(tmp_path, behavior=behavior, pr_url=pr_url)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        return bin_dir

    return _set


@pytest.fixture
def path_without_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set PATH to system dirs only (so git stays available, but gh — typically
    installed via brew at /opt/homebrew/bin — is not). Tests that need a
    different layout should monkeypatch PATH themselves."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    # /usr/bin and /bin are the POSIX defaults that ship with the OS;
    # neither carries `gh` on a stock macOS or Linux box.
    monkeypatch.setenv("PATH", f"{empty}{os.pathsep}/usr/bin{os.pathsep}/bin")
    return empty


# ─── tests ──────────────────────────────────────────────────────────


def test_open_pr_happy_path(tmp_path: Path, path_with_gh) -> None:
    path_with_gh()
    wiki, _bare = _make_wiki_with_remote(tmp_path)
    page = wiki / "sources" / "foo.md"
    page.parent.mkdir(parents=True)
    page.write_text("# foo\n")

    pr = open_pr(
        title="ingest: foo",
        body="Adds foo.md",
        branch="agent/ingest-foo",
        files=[str(page)],
        wiki_root=wiki,
    )

    assert pr.url == "https://github.com/example/wiki/pull/42"
    assert pr.branch == "agent/ingest-foo"
    # The branch was created and the file landed.
    branches = _git(wiki, "branch", "--list", "agent/ingest-foo").stdout
    assert "agent/ingest-foo" in branches
    log = _git(wiki, "log", "--oneline", "agent/ingest-foo").stdout
    assert "ingest: foo" in log


def test_open_pr_relative_paths(tmp_path: Path, path_with_gh) -> None:
    """Paths can be passed as either absolute or wiki-relative."""
    path_with_gh()
    wiki, _bare = _make_wiki_with_remote(tmp_path)
    (wiki / "sources").mkdir()
    (wiki / "sources" / "bar.md").write_text("# bar\n")

    pr = open_pr(
        title="ingest: bar",
        body="",
        branch="agent/ingest-bar",
        files=["sources/bar.md"],  # relative
        wiki_root=wiki,
    )
    assert pr.url is not None


def test_open_pr_dry_run_skips_subprocess(tmp_path: Path, path_without_gh) -> None:
    """dry_run=True returns metadata without touching git or gh."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()  # not a git repo — proves we never reached the git checks

    pr = open_pr(
        title="t",
        body="b",
        branch="x",
        files=["sources/x.md"],
        wiki_root=wiki,
        dry_run=True,
    )
    assert pr.url is None
    assert pr.branch == "x"


def test_open_pr_refuses_dirty_tree(tmp_path: Path, path_with_gh) -> None:
    path_with_gh()
    wiki, _bare = _make_wiki_with_remote(tmp_path)
    # Staged file the agent wants to commit:
    (wiki / "sources").mkdir()
    (wiki / "sources" / "foo.md").write_text("# foo\n")
    # Unrelated user-in-flight change:
    (wiki / "README.md").write_text("# wiki\n\nuser was editing this\n")

    with pytest.raises(DirtyWorkingTreeError) as exc:
        open_pr(
            title="t",
            body="b",
            branch="agent/ingest-foo",
            files=[str(wiki / "sources" / "foo.md")],
            wiki_root=wiki,
        )
    assert "README.md" in str(exc.value)


def test_open_pr_branch_collision_auto_suffixes(tmp_path: Path, path_with_gh) -> None:
    path_with_gh()
    wiki, _bare = _make_wiki_with_remote(tmp_path)
    # Pre-create the desired branch locally.
    _git(wiki, "branch", "agent/ingest-foo")
    (wiki / "sources").mkdir()
    page = wiki / "sources" / "foo.md"
    page.write_text("# foo\n")

    pr = open_pr(
        title="ingest: foo",
        body="",
        branch="agent/ingest-foo",
        files=[str(page)],
        wiki_root=wiki,
    )
    # Branch should have been suffixed with a UTC stamp.
    assert pr.branch.startswith("agent/ingest-foo-")
    assert pr.branch != "agent/ingest-foo"


def test_open_pr_gh_missing(tmp_path: Path, path_without_gh) -> None:
    wiki, _bare = _make_wiki_with_remote(tmp_path)
    (wiki / "sources").mkdir()
    page = wiki / "sources" / "foo.md"
    page.write_text("# foo\n")

    with pytest.raises(GhMissingError):
        open_pr(
            title="t",
            body="b",
            branch="agent/x",
            files=[str(page)],
            wiki_root=wiki,
        )


def test_open_pr_gh_failure_writes_audit(tmp_path: Path, path_with_gh) -> None:
    """When `gh pr create` exits non-zero, GhFailure is raised + audit row written."""
    from engine.audit.writer import AuditWriter

    path_with_gh(behavior="fail_pr")
    wiki, _bare = _make_wiki_with_remote(tmp_path)
    (wiki / "sources").mkdir()
    page = wiki / "sources" / "foo.md"
    page.write_text("# foo\n")

    audit_db = tmp_path / "audit.db"
    writer = AuditWriter(audit_db)

    with pytest.raises(GhFailure):
        open_pr(
            title="t",
            body="b",
            branch="agent/x",
            files=[str(page)],
            wiki_root=wiki,
            audit_writer=writer,
            job_id="job-123",
        )
    writer.close()

    # Verify the audit event landed.
    import sqlite3

    conn = sqlite3.connect(audit_db)
    rows = conn.execute(
        "SELECT event_type, job_id, metadata FROM audit_events WHERE event_type='pr_failed'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][1] == "job-123"
    assert "auth required" in rows[0][2]


def test_open_pr_empty_url_treated_as_failure(tmp_path: Path, path_with_gh) -> None:
    """gh exits 0 but prints no URL → GhFailure (not silent success)."""
    path_with_gh(behavior="empty_url")
    wiki, _bare = _make_wiki_with_remote(tmp_path)
    (wiki / "sources").mkdir()
    page = wiki / "sources" / "foo.md"
    page.write_text("# foo\n")

    with pytest.raises(GhFailure, match="no URL was found"):
        open_pr(
            title="t",
            body="b",
            branch="agent/x",
            files=[str(page)],
            wiki_root=wiki,
        )
