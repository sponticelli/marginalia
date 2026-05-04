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
    INDEX_FILENAME,
    ScaffoldResult,
    format_pages_listing,
    scaffold_index,
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
