"""QA gap-detection — KnowledgeGap on thin retrieval, QaAnswer otherwise."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import frontmatter
import pytest

from engine.agents.qa import KnowledgeGap, QaAnswer, qa_with_gap_detection


def _write_source(wiki_root: Path, slug: str, title: str, body: str) -> None:
    fm = {
        "title": title,
        "type": "source",
        "status": "active",
        "created": date(2026, 5, 3).isoformat(),
        "last_synced": date(2026, 5, 3).isoformat(),
        "sources": [
            {
                "ref": slug,
                "kind": "local_file",
                "captured": date(2026, 5, 3).isoformat(),
                "authority": "canonical",
            }
        ],
    }
    target = wiki_root / "sources" / f"{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(frontmatter.dumps(frontmatter.Post(content=body, **fm)), encoding="utf-8")


@pytest.mark.asyncio
async def test_returns_knowledge_gap_when_below_threshold(stub_client, tmp_path: Path) -> None:
    """Empty wiki → search returns 0 hits → KnowledgeGap, not an answer."""
    stub_client.texts = [
        json.dumps(
            {
                "reason": "no pages on EU AI Act",
                "suggested_ingests": [
                    "https://artificialintelligenceact.eu/",
                    "search:legal compliance memos",
                ],
            }
        )
    ]
    result = await qa_with_gap_detection(
        "what's our exposure to the EU AI Act?",
        wiki_root=tmp_path,
        client=stub_client,
    )
    assert isinstance(result, KnowledgeGap)
    assert len(result.suggested_ingests) >= 2
    assert any(
        "intelligenceact" in s.lower() or s.startswith("search:") for s in result.suggested_ingests
    )


@pytest.mark.asyncio
async def test_returns_answer_when_above_threshold(stub_client, tmp_path: Path) -> None:
    # Populate enough pages to clear min_pages=2 with min_score=0.0 for the test.
    for i in range(3):
        _write_source(tmp_path, f"apollo-{i}", f"Apollo {i}", f"Apollo Apollo content {i}.")

    stub_client.texts = ["Apollo info [[sources/apollo-0]].\n\nCITATIONS: [[sources/apollo-0]]"]
    result = await qa_with_gap_detection(
        "What about Apollo?",
        wiki_root=tmp_path,
        client=stub_client,
        min_pages=2,
        min_score=0.0,
    )
    assert isinstance(result, QaAnswer)


@pytest.mark.asyncio
async def test_gap_response_falls_back_when_json_malformed(stub_client, tmp_path: Path) -> None:
    stub_client.texts = ["malformed not-json response"]
    result = await qa_with_gap_detection("anything?", wiki_root=tmp_path, client=stub_client)
    assert isinstance(result, KnowledgeGap)
    assert result.suggested_ingests == []
    assert result.reason  # default fallback string
