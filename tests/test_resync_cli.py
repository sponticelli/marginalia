"""``marginalia resync`` end-to-end (Phase 4 §4.4).

Hermetic resync: seed a wiki with a previously-ingested SourcePage
(carrying a ``ref`` and ``adapter`` on its first SourceRef), then
invoke ``marginalia resync <page>`` and assert:

1. The right adapter was selected (we pre-stamp ``adapter="local_fs_text"``).
2. An ingest job was enqueued + drained, producing a refreshed page.
3. With ``--no-pr``, no PR is opened (keeps the test free of `gh`).

The full PR-creation path is exercised by `test_url_to_pr.py`; we
re-use its stubbed Anthropic fixture pattern but skip the gh mock.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.cli.main import app


@pytest.fixture
def stub_anthropic(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict]]:
    """Same pattern as tests/integration/test_url_to_pr.py — stubbed responses."""
    calls: list[dict] = []

    analysis_payload = {
        "proposed_title": "Refreshed Source",
        "proposed_type": "source",
        "summary": "After resync — re-ingested from the original source on demand.",
        "entities": [],
        "proposed_tags": ["resync"],
        "source_kind": "local_file",
    }
    synth_fm = {
        "title": "Refreshed Source",
        "type": "source",
        "status": "active",
        "created": "2026-05-04",
        "last_synced": "2026-05-04",
        "sources": [
            {
                "ref": "/will-be-overwritten",  # the actual ref is determined by the test
                "kind": "local_file",
                "captured": "2026-05-04",
                "authority": "canonical",
            }
        ],
        "tags": ["resync"],
    }

    class _Usage:
        input_tokens = 50
        output_tokens = 25

    class _Block:
        def __init__(self, text: str):
            self.text = text
            self.type = "text"

    class _Resp:
        def __init__(self, text: str):
            self.content = [_Block(text)]
            self.usage = _Usage()
            self.stop_reason = "end_turn"

    synth_response = (
        f"<frontmatter>\n{json.dumps(synth_fm)}\n</frontmatter>\n"
        "<body>\n# Refreshed Source\n\nResynced body.\n</body>"
    )

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
            if "<analysis>" in user_msg:
                return _Resp(synth_response)
            return _Resp(json.dumps(analysis_payload))

    class _StubClient:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _StubClient)
    yield calls


def _seed_wiki(tmp_path: Path) -> Path:
    """Create a wiki with purpose/AGENTS + an existing source page."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "purpose.md").write_text("# Purpose\n\nResync test wiki.\n")
    (wiki / "AGENTS.md").write_text("# AGENTS\n\nUse customer.\n")
    return wiki


def _write_existing_source_page(
    wiki: Path,
    *,
    page_path: str,
    ref: str,
    adapter: str | None,
) -> Path:
    """Write a SourcePage frontmatter+body that resync will pick up."""
    src_dir = wiki / Path(page_path).parent
    src_dir.mkdir(parents=True, exist_ok=True)
    out = wiki / f"{page_path}.md"

    sources_payload = [
        {
            "ref": ref,
            "kind": "local_file",
            "captured": "2026-05-01",
            "authority": "canonical",
        }
    ]
    if adapter is not None:
        sources_payload[0]["adapter"] = adapter

    fm = {
        "title": "Original Source",
        "type": "source",
        "status": "active",
        "created": "2026-05-01",
        "last_synced": "2026-05-01",
        "sources": sources_payload,
    }
    body = "# Original\n\nOriginal body, pre-resync.\n"
    # Mirror frontmatter format that python-frontmatter writes (YAML).
    import yaml  # type: ignore[import-not-found]

    fm_yaml = yaml.safe_dump(fm, sort_keys=False)
    out.write_text(f"---\n{fm_yaml}---\n{body}", encoding="utf-8")
    return out


def test_resync_refreshes_page_and_reuses_stored_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_anthropic: list[dict],
) -> None:
    """Page with ``adapter='local_fs_text'`` resyncs cleanly and updates the file."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("MARGINALIA_WIKI", raising=False)

    wiki = _seed_wiki(tmp_path)
    monkeypatch.setenv("WIKI_CONTENT_REPO", str(wiki))

    # Create the underlying source file so the local_fs_text adapter
    # can re-read it during resync.
    src_file = tmp_path / "source.md"
    src_file.write_text("# Refreshed source\n\nNew body since last ingest.\n")

    page_md = _write_existing_source_page(
        wiki,
        page_path="sources/test/original",
        ref=str(src_file),
        adapter="local_fs_text",
    )
    original_text = page_md.read_text()

    runner = CliRunner()
    result = runner.invoke(app, ["resync", "sources/test/original", "--no-pr"])

    if result.exit_code != 0:
        print("RESYNC OUTPUT:\n", result.output)
        if result.exception:
            import traceback

            traceback.print_exception(
                type(result.exception),
                result.exception,
                result.exception.__traceback__,
            )
    assert result.exit_code == 0
    # The CLI prints the stored adapter so the user can confirm routing.
    assert "local_fs_text" in result.output
    assert "resyncing via stored adapter" in result.output

    # The page on disk should have been refreshed (the LLM stub returns
    # "Refreshed Source" as the title, distinct from the original).
    # The page may be at a new path because synthesis derives the path from
    # the title. Either the file is updated in place OR a new file exists.
    refreshed_pages = list(wiki.rglob("*.md"))
    refreshed_titles = []
    for p in refreshed_pages:
        if p.name in ("purpose.md", "AGENTS.md"):
            continue
        refreshed_titles.append(p.read_text())
    # At least one of those bodies should mention "Refreshed Source".
    assert any(
        "Refreshed Source" in t for t in refreshed_titles
    ), f"expected a refreshed page; original was: {original_text[:100]!r}"


def test_resync_falls_back_when_no_stored_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_anthropic: list[dict],
) -> None:
    """A page with no `adapter` field warns the user and still re-runs via dispatch."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("MARGINALIA_WIKI", raising=False)

    wiki = _seed_wiki(tmp_path)
    monkeypatch.setenv("WIKI_CONTENT_REPO", str(wiki))

    src_file = tmp_path / "fallback.md"
    src_file.write_text("# Fallback\n\nNo adapter field.\n")
    _write_existing_source_page(
        wiki,
        page_path="sources/test/fallback",
        ref=str(src_file),
        adapter=None,  # back-compat path
    )

    runner = CliRunner()
    result = runner.invoke(app, ["resync", "sources/test/fallback", "--no-pr"])
    assert result.exit_code == 0, result.output
    assert "no stored adapter" in result.output


def test_resync_unknown_page_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bogus page path → clear error, exit 1."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("MARGINALIA_WIKI", raising=False)
    wiki = _seed_wiki(tmp_path)
    monkeypatch.setenv("WIKI_CONTENT_REPO", str(wiki))

    runner = CliRunner()
    result = runner.invoke(app, ["resync", "sources/no-such-page", "--no-pr"])
    assert result.exit_code == 1
    assert "no page at" in result.output.lower()
