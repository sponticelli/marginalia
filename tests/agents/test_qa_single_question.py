"""QA single-question — search → answer → verify citations."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import frontmatter
import pytest

from engine.agents.qa import qa_single_question


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
async def test_qa_single_question_returns_answer_with_citations(
    stub_client, tmp_path: Path
) -> None:
    _write_source(tmp_path, "apollo-q2", "Apollo Q2", "Apollo launches Q2 2026.")
    _write_source(tmp_path, "apollo-q3", "Apollo Q3", "Apollo postponed to Q3 2026.")

    stub_client.texts = [
        "Apollo's launch is currently scheduled for Q2 2026 [[sources/apollo-q2]] but a "
        "later note pushes it to Q3 [[sources/apollo-q3]].\n\n"
        "CITATIONS: [[sources/apollo-q2]] [[sources/apollo-q3]]"
    ]

    answer = await qa_single_question(
        "When does Apollo launch?", wiki_root=tmp_path, client=stub_client
    )
    assert "Apollo" in answer.answer
    assert sorted(answer.citations) == ["sources/apollo-q2", "sources/apollo-q3"]
    assert answer.dangling_citations == []
    assert answer.cost_usd > 0
    assert "CITATIONS:" not in answer.answer  # citations line stripped from answer body


@pytest.mark.asyncio
async def test_qa_single_question_flags_dangling_citations(stub_client, tmp_path: Path) -> None:
    _write_source(tmp_path, "apollo-q2", "Apollo Q2", "Apollo launches Q2 2026.")

    stub_client.texts = [
        "Decision was made [[sources/nonexistent-page]] last quarter.\n\n"
        "CITATIONS: [[sources/nonexistent-page]]"
    ]
    answer = await qa_single_question(
        "What was the decision?", wiki_root=tmp_path, client=stub_client
    )
    assert "sources/nonexistent-page" in answer.dangling_citations


@pytest.mark.asyncio
async def test_qa_single_question_handles_no_results(stub_client, tmp_path: Path) -> None:
    """Empty wiki → search returns no hits → agent answers with no citations."""
    stub_client.texts = ["I don't have any pages on this topic.\n\nCITATIONS:"]
    answer = await qa_single_question(
        "What about EU AI Act?", wiki_root=tmp_path, client=stub_client
    )
    assert answer.citations == []
    assert answer.retrieved_paths == []
