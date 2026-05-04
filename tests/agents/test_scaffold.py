"""Scaffold agent — index regeneration from page state.

Three things to verify:

1. ``format_pages_listing`` produces the stable, type-grouped, title-sorted
   string the prompt expects.
2. ``scaffold_index`` walks the canonical poc-wiki, calls the (stubbed)
   LLM once, and returns a ``ScaffoldResult`` with the model's output.
3. With ``write=True`` the file lands at ``<wiki>/index.md``;
   ``write=False`` returns content without touching disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.agents.scaffold import (
    AGENTS_FILENAME,
    DASHBOARD_FILENAME,
    INDEX_FILENAME,
    PROPOSED_SUFFIX,
    PURPOSE_FILENAME,
    SUPPORTED_TARGETS,
    ScaffoldResult,
    format_pages_listing,
    scaffold_agents,
    scaffold_dashboard,
    scaffold_index,
    scaffold_purpose,
)
from engine.models.wiki_config import MarginaliaConfig
from engine.utils.backlink_rewrite import copy_wiki_to
from engine.utils.wiki_walker import walk_wiki
from tests.conftest import StubAnthropicClient

POC_WIKI = Path(__file__).resolve().parents[2] / "notebooks" / "data" / "poc-wiki"


@pytest.fixture
def wiki_copy(tmp_path: Path) -> Path:
    """Throwaway copy of the canonical PoC wiki — scaffold_index writes to it."""
    return copy_wiki_to(POC_WIKI, tmp_path / "wiki")


def test_format_pages_listing_groups_by_type(wiki_copy: Path) -> None:
    """The listing string is sorted (type-order, title) so the LLM gets a stable layout."""
    pages = list(walk_wiki(wiki_copy))
    listing = format_pages_listing(pages)
    lines = listing.splitlines()

    # Every line follows the canonical format.
    for line in lines:
        assert line.startswith("- type=")
        assert "wikilink=" in line
        assert "title=" in line

    # Type-order: entities (none in poc-wiki) → concepts → decisions → sources → ...
    # Verify that all `decision` lines come before all `source` lines.
    decision_lines = [i for i, line in enumerate(lines) if "type=decision" in line]
    source_lines = [i for i, line in enumerate(lines) if "type=source" in line]
    if decision_lines and source_lines:
        assert max(decision_lines) < min(source_lines)


@pytest.mark.asyncio
async def test_scaffold_index_writes_file(wiki_copy: Path) -> None:
    """End-to-end: stub LLM → scaffold_index → file lands at <wiki>/index.md."""
    config = MarginaliaConfig.load(wiki_copy)
    canned_index = (
        "# Marginalia PoC Wiki\n\n"
        "Test content.\n\n"
        "## Concepts\n\n"
        "- [[knowledge/concepts/ingest-pipeline]] — pipeline\n\n"
        "## Decisions\n\n"
        "- [[knowledge/decisions/apollo-q2-ship]] — ship q2\n"
    )
    client = StubAnthropicClient(texts=[canned_index])

    result = await scaffold_index(wiki_copy, config=config, client=client)

    assert isinstance(result, ScaffoldResult)
    assert result.target == "index"
    assert result.pages_indexed > 0
    assert result.out_path == wiki_copy / INDEX_FILENAME
    assert result.out_path.is_file()
    written = result.out_path.read_text()
    assert "Marginalia PoC Wiki" in written
    assert "[[knowledge/decisions/apollo-q2-ship]]" in written


@pytest.mark.asyncio
async def test_scaffold_index_dry_run_does_not_write(wiki_copy: Path) -> None:
    """write=False returns content without modifying disk."""
    config = MarginaliaConfig.load(wiki_copy)
    client = StubAnthropicClient(texts=["# dry run output\n"])

    # Make sure no index.md exists pre-call.
    target = wiki_copy / INDEX_FILENAME
    assert not target.exists()

    result = await scaffold_index(wiki_copy, config=config, client=client, write=False)

    assert "dry run output" in result.content
    assert not target.exists()


@pytest.mark.asyncio
async def test_scaffold_index_calls_llm_with_listing_in_user_prompt(
    wiki_copy: Path,
) -> None:
    """Verify the user message includes the rendered page listing — proves wiring."""
    config = MarginaliaConfig.load(wiki_copy)
    client = StubAnthropicClient(texts=["# fake\n"])

    await scaffold_index(wiki_copy, config=config, client=client, write=False)

    assert len(client.calls) == 1
    user_msg = client.calls[0]["messages"][0]["content"]
    # The listing format is unique enough that finding 'type=decision' or
    # 'wikilink=knowledge/decisions/' in the prompt confirms the listing was inlined.
    assert "type=decision" in user_msg
    assert "wikilink=knowledge/decisions/" in user_msg


# ─── scaffold_purpose ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scaffold_purpose_writes_proposed_by_default(wiki_copy: Path) -> None:
    """Default write=False writes to purpose.md.proposed, leaving the real file untouched."""
    config = MarginaliaConfig.load(wiki_copy)
    canned = (
        "# Marginalia PoC Wiki\n\n"
        "Tracks the Apollo launch decisions and the marginalia engine architecture.\n\n"
        "## In scope\n\n- Apollo launch decisions\n- Engine architecture\n"
    )
    client = StubAnthropicClient(texts=[canned])

    real_file = wiki_copy / PURPOSE_FILENAME
    real_before = real_file.read_text()  # poc-wiki ships purpose.md

    result = await scaffold_purpose(wiki_copy, config=config, client=client)

    proposed = wiki_copy / (PURPOSE_FILENAME + PROPOSED_SUFFIX)
    assert result.target == "purpose"
    assert result.out_path == proposed
    assert proposed.is_file()
    # Real file is untouched — the recommend-then-confirm contract.
    assert real_file.read_text() == real_before
    assert "Apollo launch decisions" in proposed.read_text()


@pytest.mark.asyncio
async def test_scaffold_purpose_write_true_overwrites_real_file(wiki_copy: Path) -> None:
    """write=True overwrites purpose.md directly — the explicit --apply path."""
    config = MarginaliaConfig.load(wiki_copy)
    canned = "# New Purpose\n\nFresh content.\n"
    client = StubAnthropicClient(texts=[canned])

    real_file = wiki_copy / PURPOSE_FILENAME
    result = await scaffold_purpose(wiki_copy, config=config, client=client, write=True)

    assert result.out_path == real_file
    assert real_file.read_text().strip().endswith("Fresh content.")
    # No .proposed file when applied directly.
    assert not (wiki_copy / (PURPOSE_FILENAME + PROPOSED_SUFFIX)).exists()


@pytest.mark.asyncio
async def test_scaffold_purpose_passes_current_purpose_to_llm(wiki_copy: Path) -> None:
    """The user prompt embeds the current purpose.md body — agents need both
    the wiki state AND the existing scope claim to spot drift."""
    config = MarginaliaConfig.load(wiki_copy)
    client = StubAnthropicClient(texts=["# proposal\n"])

    await scaffold_purpose(wiki_copy, config=config, client=client)

    user_msg = client.calls[0]["messages"][0]["content"]
    # poc-wiki/purpose.md mentions "Marginalia" — confirm it landed in the prompt.
    assert "Marginalia" in user_msg
    # And the page listing is also present.
    assert "wikilink=knowledge/decisions/" in user_msg


# ─── scaffold_agents ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scaffold_agents_writes_proposed_by_default(wiki_copy: Path) -> None:
    """Default write=False writes to AGENTS.md.proposed, never overwrites the real file."""
    config = MarginaliaConfig.load(wiki_copy)
    canned = "# AGENTS\n\nUse customer, not client.\n\n## Drift\n\n- detected nothing\n"
    client = StubAnthropicClient(texts=[canned])

    real_file = wiki_copy / AGENTS_FILENAME
    real_before = real_file.read_text()

    result = await scaffold_agents(wiki_copy, config=config, client=client)

    proposed = wiki_copy / (AGENTS_FILENAME + PROPOSED_SUFFIX)
    assert result.target == "agents"
    assert result.out_path == proposed
    assert proposed.is_file()
    assert real_file.read_text() == real_before


@pytest.mark.asyncio
async def test_scaffold_agents_write_true_overwrites_real_file(wiki_copy: Path) -> None:
    """write=True replaces AGENTS.md directly."""
    config = MarginaliaConfig.load(wiki_copy)
    canned = "# AGENTS\n\nFresh style guide.\n"
    client = StubAnthropicClient(texts=[canned])

    real_file = wiki_copy / AGENTS_FILENAME
    result = await scaffold_agents(wiki_copy, config=config, client=client, write=True)

    assert result.out_path == real_file
    assert real_file.read_text().strip().endswith("Fresh style guide.")


@pytest.mark.asyncio
async def test_scaffold_agents_includes_page_excerpts_in_prompt(wiki_copy: Path) -> None:
    """Agents prompt sends page body excerpts — needed to spot terminology drift."""
    config = MarginaliaConfig.load(wiki_copy)
    client = StubAnthropicClient(texts=["# AGENTS\n"])

    await scaffold_agents(wiki_copy, config=config, client=client)

    user_msg = client.calls[0]["messages"][0]["content"]
    # Confirm the excerpt section landed (per-page <page path="..."> blocks).
    assert "<page path=" in user_msg
    # And the current AGENTS.md body is in the prompt.
    assert "customer" in user_msg.lower()


# ─── scaffold_dashboard ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scaffold_dashboard_writes_dashboard_md(wiki_copy: Path) -> None:
    """Default write=True regenerates <wiki>/dashboard.md without an LLM call."""
    result = await scaffold_dashboard(wiki_copy)

    assert isinstance(result, ScaffoldResult)
    assert result.target == "dashboard"
    assert result.out_path == wiki_copy / DASHBOARD_FILENAME
    assert result.out_path.is_file()
    assert result.cost_usd == 0.0
    assert result.tokens_in == 0
    assert result.tokens_out == 0
    assert result.model == "(deterministic)"

    body = result.out_path.read_text()
    assert "Wiki Dashboard" in body
    # Dataview blocks are part of the deterministic template.
    assert "```dataview" in body


@pytest.mark.asyncio
async def test_scaffold_dashboard_dry_run_skips_write(wiki_copy: Path) -> None:
    """write=False returns content but doesn't touch disk."""
    target = wiki_copy / DASHBOARD_FILENAME
    assert not target.exists()

    result = await scaffold_dashboard(wiki_copy, write=False)

    assert "Wiki Dashboard" in result.content
    assert not target.exists()


@pytest.mark.asyncio
async def test_scaffold_dashboard_is_idempotent_modulo_timestamp(wiki_copy: Path) -> None:
    """Two consecutive runs differ only in the embedded ``Generated:`` line."""
    r1 = await scaffold_dashboard(wiki_copy)
    r2 = await scaffold_dashboard(wiki_copy)
    # Strip the timestamp line and compare the rest.
    body1 = "\n".join(line for line in r1.content.splitlines() if "Generated:" not in line)
    body2 = "\n".join(line for line in r2.content.splitlines() if "Generated:" not in line)
    assert body1 == body2


# ─── targets registry ──────────────────────────────────────────────


def test_supported_targets_lists_all_four() -> None:
    """SUPPORTED_TARGETS now covers index, purpose, agents, dashboard."""
    assert set(SUPPORTED_TARGETS) == {"index", "purpose", "agents", "dashboard"}
