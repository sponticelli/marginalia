"""QA decomposition — split / fall back / parallel gather."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import frontmatter
import pytest

from engine.agents.qa import SubQuery, decompose_query, qa_with_decomposition


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
async def test_decompose_compound_question_splits(stub_client) -> None:
    stub_client.texts = [
        json.dumps(
            [
                {"text": "what about Q2 pricing?", "priority": 1},
                {"text": "how does it affect Apollo?", "priority": 2},
            ]
        )
    ]
    sub_queries = await decompose_query(
        "what did we decide about Q2 pricing AND how does it affect Apollo?",
        client=stub_client,
    )
    assert len(sub_queries) == 2
    assert sub_queries[0].text == "what about Q2 pricing?"


@pytest.mark.asyncio
async def test_decompose_single_returns_one_subquery(stub_client) -> None:
    stub_client.texts = [json.dumps([{"text": "what is the apollo launch status?", "priority": 1}])]
    sub_queries = await decompose_query("what is the apollo launch status?", client=stub_client)
    assert len(sub_queries) == 1
    assert isinstance(sub_queries[0], SubQuery)


@pytest.mark.asyncio
async def test_decompose_falls_back_on_malformed_json(stub_client) -> None:
    """Malformed JSON → fall back to a single-item list with the original question."""
    stub_client.texts = ["this is not JSON at all"]
    sub_queries = await decompose_query("any question?", client=stub_client)
    assert len(sub_queries) == 1
    assert sub_queries[0].text == "any question?"


@pytest.mark.asyncio
async def test_qa_with_decomposition_runs_subqueries_and_merges(
    stub_client, tmp_path: Path
) -> None:
    _write_source(tmp_path, "apollo-q2", "Apollo Q2", "Apollo Q2 2026 launch.")
    _write_source(tmp_path, "pricing", "Pricing decision", "Q2 pricing locked.")

    # Decomposition response, then two single-question answers.
    stub_client.texts = [
        json.dumps(
            [
                {"text": "what about Q2 pricing?", "priority": 1},
                {"text": "what about Apollo Q2?", "priority": 2},
            ]
        ),
        "Q2 pricing was locked [[sources/pricing]].\n\nCITATIONS: [[sources/pricing]]",
        "Apollo Q2 launches as planned [[sources/apollo-q2]].\n\nCITATIONS: [[sources/apollo-q2]]",
    ]
    answer = await qa_with_decomposition(
        "what did we decide about Q2 pricing AND Apollo?",
        wiki_root=tmp_path,
        client=stub_client,
    )
    # Both sub-queries' citations appear in the merged answer.
    assert "sources/pricing" in answer.citations
    assert "sources/apollo-q2" in answer.citations
    # Body has both sub-question headers.
    assert "Sub-question" in answer.answer
    assert answer.answer.count("Sub-question") == 2


@pytest.mark.asyncio
async def test_qa_with_decomposition_degenerate_passes_through(stub_client, tmp_path: Path) -> None:
    _write_source(tmp_path, "apollo-q2", "Apollo Q2", "Apollo Q2 launches.")
    stub_client.texts = [
        json.dumps([{"text": "when does Apollo launch?", "priority": 1}]),
        "Q2 2026 [[sources/apollo-q2]].\n\nCITATIONS: [[sources/apollo-q2]]",
    ]
    answer = await qa_with_decomposition(
        "when does Apollo launch?", wiki_root=tmp_path, client=stub_client
    )
    assert "Sub-question" not in answer.answer  # passthrough, not merged shape
    assert answer.citations == ["sources/apollo-q2"]
