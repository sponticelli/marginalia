"""End-to-end: ``marginalia add`` → ``marginalia ingest --wait`` opens a real PR.

The hermetic version of Phase 1.5 from the completion plan: spin up a
temp wiki content repo, a temp inbox, a stubbed Anthropic SDK, and a
PATH-injected ``gh`` mock. Run the two CLI commands. Assert the page
landed on a new branch and that ``audit.db`` recorded the ingest +
cost rows.

Why this test exists: the 229 unit/component tests prove each
subsystem in isolation, but none verify the full chain holds together
— which is the question that distinguishes "Marginalia ships" from
"Marginalia could ship if you wired it up." This is the green-light
test for Phase 1.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.cli.main import app

# ─── fixtures ───────────────────────────────────────────────────────


def _git(repo: Path, *argv: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *argv],
        capture_output=True,
        text=True,
        check=True,
    )


def _make_wiki_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Create a working wiki repo + bare remote + initial commit on main.

    Seed it with the minimum the engine needs at runtime:
    - ``purpose.md`` and ``AGENTS.md`` (loaded by ``MarginaliaConfig``).
    """
    bare = tmp_path / "wiki-remote.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(bare)], check=True)

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git(wiki, "init", "--initial-branch=main")
    _git(wiki, "config", "user.email", "test@example.com")
    _git(wiki, "config", "user.name", "Test")
    (wiki / "purpose.md").write_text(
        "# Purpose\n\nIntegration-test wiki for marginalia.\n",
    )
    (wiki / "AGENTS.md").write_text(
        "# AGENTS\n\nUse customer, not client.\n",
    )
    _git(wiki, "add", "purpose.md", "AGENTS.md")
    _git(wiki, "commit", "-m", "initial")
    _git(wiki, "remote", "add", "origin", str(bare))
    _git(wiki, "push", "-u", "origin", "main")
    return wiki, bare


def _make_gh_mock(tmp_path: Path, pr_url: str) -> Path:
    """Write an executable ``gh`` shell stub to a bin dir; return the dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "gh version 2.0.0"; exit 0; fi\n'
        f'echo "{pr_url}"\n'
        "exit 0\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


@pytest.fixture
def stub_anthropic(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict]]:
    """Replace ``anthropic.Anthropic`` with a stub returning canned analyze + synth.

    Yields the call log so tests can assert the expected calls happened.
    The stub responds to two patterns:

    1. The analyze step (Haiku) — request whose system prompt mentions
       "ingest_analyze" or whose user message contains the source
       content. Responds with a SourceAnalysis JSON.
    2. The synthesize step (Sonnet) — anything else. Responds with
       ``<frontmatter>{...}</frontmatter><body>...</body>``.

    Routing by user-message keywords keeps the stub generic without
    hardcoding the prompt details.
    """
    calls: list[dict] = []

    analysis_payload = {
        "proposed_title": "Test Source",
        "proposed_type": "source",
        "summary": "This is a deterministic summary used by the integration test.",
        "entities": ["Apollo"],
        "proposed_tags": ["test", "integration"],
        "source_kind": "local_file",
    }
    synthesize_payload_fm = {
        "title": "Test Source",
        "type": "source",
        "status": "active",
        "created": "2026-05-04",
        "last_synced": "2026-05-04",
        "sources": [
            {
                "ref": "test_source.md",
                "kind": "local_file",
                "captured": "2026-05-04",
                "authority": "canonical",
            }
        ],
        "tags": ["test", "integration"],
    }
    synthesize_response = (
        f"<frontmatter>\n{json.dumps(synthesize_payload_fm)}\n</frontmatter>\n"
        "<body>\n# Test Source\n\nIntegration-test body.\n</body>"
    )

    class _Usage:
        input_tokens = 100
        output_tokens = 50

    class _Block:
        def __init__(self, text: str):
            self.text = text
            self.type = "text"

    class _Resp:
        def __init__(self, text: str):
            self.content = [_Block(text)]
            self.usage = _Usage()
            self.stop_reason = "end_turn"

    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            user_msg = ""
            for m in kwargs.get("messages", []):
                if m.get("role") == "user":
                    content = m["content"]
                    if isinstance(content, str):
                        user_msg = content
                    elif isinstance(content, list):
                        user_msg = " ".join(
                            b.get("text", "") for b in content if isinstance(b, dict)
                        )
            # Route by the unambiguous tag the prompts use:
            # ``ingest_analyze`` user template wraps the source in
            # ``<source kind=...>`` and ``ingest_synthesize`` wraps
            # the analyze JSON in ``<analysis>``.
            if "<analysis>" in user_msg:
                return _Resp(synthesize_response)
            return _Resp(json.dumps(analysis_payload))

    class _StubClient:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _StubClient)
    yield calls


# ─── the test ───────────────────────────────────────────────────────


def test_add_then_ingest_wait_opens_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_anthropic: list[dict],
) -> None:
    """End-to-end happy path: stage a markdown file, ingest with --wait, see a PR URL."""
    # ── arrange ─────────────────────────────────────────────────
    wiki, bare = _make_wiki_with_remote(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    bin_dir = _make_gh_mock(tmp_path, pr_url="https://github.com/example/wiki/pull/7")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("WIKI_CONTENT_REPO", str(wiki))
    monkeypatch.setenv("WIKI_RAW_PATH", str(inbox))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-used")

    src_file = tmp_path / "test_source.md"
    src_file.write_text(
        "# Test source\n\nApollo Q2 launch confirmation: shipping 2026-05-15.\n",
    )

    runner = CliRunner()

    # ── act ────────────────────────────────────────────────────
    add_result = runner.invoke(app, ["add", str(src_file)])
    assert add_result.exit_code == 0, add_result.output
    assert "staged file" in add_result.output

    ingest_result = runner.invoke(app, ["ingest", "--wait"])
    if ingest_result.exit_code != 0 or "opened PR" not in ingest_result.output:
        # Surface full output + exception for fast diagnosis on failure.
        print("INGEST OUTPUT:\n", ingest_result.output)
        if ingest_result.exception:
            import traceback

            traceback.print_exception(
                type(ingest_result.exception),
                ingest_result.exception,
                ingest_result.exception.__traceback__,
            )
    assert ingest_result.exit_code == 0
    assert "opened PR →" in ingest_result.output
    assert "https://github.com/example/wiki/pull/7" in ingest_result.output

    # ── assert: page landed on a fresh branch ──────────────────
    branches = subprocess.run(
        ["git", "-C", str(wiki), "branch", "--list", "agent/ingest-*"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "agent/ingest-" in branches

    # The page exists in the wiki repo, in the synthesized location.
    sources_dir = wiki / "sources"
    md_files = list(sources_dir.rglob("*.md")) if sources_dir.exists() else []
    assert md_files, "expected at least one synthesized source page in wiki/sources/"

    # ── assert: audit DB recorded the ingest + cost ────────────
    audit_db = wiki / ".wiki" / "audit.db"
    assert audit_db.exists()
    conn = sqlite3.connect(audit_db)
    try:
        ingest_rows = conn.execute(
            "SELECT source_ref, page_paths, cost_usd FROM ingest_history"
        ).fetchall()
        cost_rows = conn.execute(
            "SELECT agent, model, tokens_in, tokens_out FROM cost_records"
        ).fetchall()
    finally:
        conn.close()

    assert len(ingest_rows) == 1
    source_ref, page_paths_json, cost_usd = ingest_rows[0]
    # The ingest payload's "input" is the file path we staged.
    assert source_ref.endswith("test_source.md") or "test_source" in source_ref
    page_paths = json.loads(page_paths_json)
    assert len(page_paths) == 1
    assert cost_usd >= 0.0  # could be 0 if pricing table doesn't know the test model

    # Cost rows: analyze (ingest agent) + synthesize (synthesis agent).
    agents = sorted({row[0] for row in cost_rows})
    assert "ingest" in agents
    assert "synthesis" in agents

    # ── assert: stubbed Anthropic actually got called ──────────
    assert len(stub_anthropic) >= 2, "expected ≥1 analyze + ≥1 synth call"
