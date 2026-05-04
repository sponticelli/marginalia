"""Lint agent — pure-Python detectors + Opus contradiction parsing with stub client."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from engine.agents.lint import (
    Contradiction,
    LintReport,
    detect_contradictions,
    detect_orphans,
    detect_stale,
    format_lint_report_md,
    format_pages_block,
)
from engine.utils.wiki_walker import walk_wiki
from tests.conftest import StubAnthropicClient

POC_WIKI = Path(__file__).resolve().parents[1] / "notebooks" / "data" / "poc-wiki"


def test_detect_stale_flags_apollo_q2_ship() -> None:
    """apollo-q2-ship.md has last_synced=2026-04-15; relative to 2026-05-03 that's 18 days."""
    pages = list(walk_wiki(POC_WIKI))
    stale = detect_stale(pages, threshold_days=14, today=date(2026, 5, 3))
    stale_links = {f.wikilink for f in stale}

    assert "knowledge/decisions/apollo-q2-ship" in stale_links
    for finding in stale:
        if finding.wikilink == "knowledge/decisions/apollo-q2-ship":
            assert finding.days_stale == 18


def test_detect_stale_high_threshold_returns_empty() -> None:
    """A threshold larger than every gap means no findings."""
    pages = list(walk_wiki(POC_WIKI))
    none_stale = detect_stale(pages, threshold_days=10_000, today=date(2026, 5, 3))
    assert none_stale == []


def test_format_pages_block_includes_only_decisions_and_concepts() -> None:
    pages = list(walk_wiki(POC_WIKI))
    block = format_pages_block(pages)

    # Decisions + concepts are wrapped as <page ...> blocks.
    assert '<page id="knowledge/decisions/apollo-q2-ship"' in block
    assert '<page id="knowledge/decisions/apollo-q3-postpone"' in block
    assert '<page id="knowledge/concepts/ingest-pipeline"' in block
    # Sources and entities must NOT have their own <page> envelopes.
    assert '<page id="sources/' not in block
    assert '<page id="knowledge/entities/' not in block


def test_detect_contradictions_parses_stub_json() -> None:
    """Stub Opus response → list[Contradiction]."""
    pages = list(walk_wiki(POC_WIKI))
    canned = (
        "<thinking>\nWalk pairwise...\n</thinking>\n\n"
        "```json\n"
        + json.dumps(
            [
                {
                    "page_a": "knowledge/decisions/apollo-q2-ship",
                    "page_b": "knowledge/decisions/apollo-q3-postpone",
                    "subject": "Apollo launch quarter",
                    "why": "Q2 vs Q3 commitments are mutually exclusive.",
                    "severity": "high",
                    "evidence": ["ship in Q2 2026", "postponed to Q3 2026"],
                }
            ]
        )
        + "\n```\n"
    )
    client = StubAnthropicClient(texts=[canned])
    contradictions, tokens_in, tokens_out = detect_contradictions(pages, client=client)

    assert len(contradictions) == 1
    c = contradictions[0]
    assert isinstance(c, Contradiction)
    assert c.severity == "high"
    assert c.page_a == "knowledge/decisions/apollo-q2-ship"
    assert tokens_in == 100
    assert tokens_out == 20


def test_detect_contradictions_falls_back_on_bad_json() -> None:
    """Malformed JSON → empty list, not a crash."""
    pages = list(walk_wiki(POC_WIKI))
    client = StubAnthropicClient(texts=["I cannot find any contradictions in this wiki."])
    contradictions, _, _ = detect_contradictions(pages, client=client)
    assert contradictions == []


def test_format_lint_report_md_renders_all_sections() -> None:
    pages = list(walk_wiki(POC_WIKI))
    stale = detect_stale(pages, threshold_days=14, today=date(2026, 5, 3))
    orphans = detect_orphans(pages)

    report = LintReport(
        total_pages=len(pages),
        stale_pages=stale,
        contradictions=[
            Contradiction(
                page_a="knowledge/decisions/apollo-q2-ship",
                page_b="knowledge/decisions/apollo-q3-postpone",
                subject="Apollo launch quarter",
                why="Q2 vs Q3 are exclusive.",
                severity="high",
                evidence=["Q2 2026", "Q3 2026"],
            )
        ],
        orphans=orphans,
        model="claude-opus-4-7",
        tokens_in=1500,
        tokens_out=400,
        cost_usd=0.0525,
        wall_seconds=12.3,
    )
    md = format_lint_report_md(report, generated_on=date(2026, 5, 3))

    assert "# Lint report" in md
    assert "## Stale pages" in md
    assert "## Contradictions found (1)" in md
    assert "## Orphan pages" in md
    assert "Apollo launch quarter" in md
    assert "claude-opus-4-7" in md
