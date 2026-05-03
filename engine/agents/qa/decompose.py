"""QA decomposition path (design §7.1, behavior 2).

Compound questions get split into focused sub-queries that run in
parallel. The decomposition itself is a Sonnet 4.6 call; the per-sub
QA calls reuse ``qa_single_question``. A final "merge" call
synthesizes the sub-answers.

Falls back to single-question path on any decomposition failure
(malformed JSON, exception, empty list).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from engine.agents.qa.single_question import (
    DEFAULT_MAX_TOKENS as SINGLE_DEFAULT_MAX_TOKENS,
)
from engine.agents.qa.single_question import (
    DEFAULT_MODEL as SINGLE_DEFAULT_MODEL,
)
from engine.agents.qa.single_question import QaAnswer, qa_single_question
from engine.prompts import Prompt, load_prompt
from engine.utils.api_compat import temperature_kwargs

if TYPE_CHECKING:
    from anthropic import Anthropic

PROMPT_NAME = "qa_decompose"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 512


class SubQuery(BaseModel):
    """One focused sub-question from decomposition."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    priority: int = Field(default=1, ge=1)


def _extract_json_array(raw: str) -> list[dict] | None:
    """Best-effort JSON-array extraction from a possibly-wrapped response."""
    text = raw.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, list) else None


async def decompose_query(
    question: str,
    *,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    prompt: Prompt | None = None,
) -> list[SubQuery]:
    """Classify + split a question. Always returns at least one SubQuery."""
    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    prompt = prompt or load_prompt(PROMPT_NAME)
    user_msg = prompt.user_template.format(question=question)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        **temperature_kwargs(model),
        system=prompt.system,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = resp.content[0].text
    data = _extract_json_array(raw)
    if not data:
        return [SubQuery(text=question, priority=1)]

    sub_queries: list[SubQuery] = []
    for item in data:
        try:
            sub_queries.append(SubQuery.model_validate(item))
        except Exception:  # noqa: BLE001
            continue

    if not sub_queries:
        return [SubQuery(text=question, priority=1)]
    return sub_queries


def _merge_answers(question: str, sub_answers: list[QaAnswer]) -> tuple[str, list[str], list[str]]:
    """Stitch sub-answers into one merged body. No second LLM call."""
    parts: list[str] = []
    all_citations: set[str] = set()
    all_retrieved: set[str] = set()
    for sa in sub_answers:
        sub_label = f"### Sub-question: {sa.question}\n\n{sa.answer}"
        parts.append(sub_label)
        all_citations.update(sa.citations)
        all_retrieved.update(sa.retrieved_paths)
    body = "\n\n".join(parts)
    return body, sorted(all_citations), sorted(all_retrieved)


async def qa_with_decomposition(
    question: str,
    *,
    wiki_root: Path,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    decompose_prompt: Prompt | None = None,
    single_prompt: Prompt | None = None,
    top_k: int = 5,
) -> QaAnswer:
    """Decompose, run sub-queries in parallel, merge.

    Falls back to single-question if decomposition returns one item
    (the degenerate case is the same as the single-question path,
    just with extra LLM cost paid for the decomposition call).
    """
    sub_queries = await decompose_query(
        question, client=client, model=model, prompt=decompose_prompt
    )

    sub_answers = await asyncio.gather(
        *(
            qa_single_question(
                sq.text,
                wiki_root=wiki_root,
                client=client,
                model=SINGLE_DEFAULT_MODEL,
                max_tokens=SINGLE_DEFAULT_MAX_TOKENS,
                top_k=top_k,
                prompt=single_prompt,
            )
            for sq in sub_queries
        )
    )

    if len(sub_answers) == 1:
        # Pass through unchanged — caller paid for decomposition; that cost is sunk.
        return sub_answers[0]

    body, citations, retrieved = _merge_answers(question, sub_answers)
    total_in = sum(sa.tokens_in for sa in sub_answers)
    total_out = sum(sa.tokens_out for sa in sub_answers)
    total_cost = sum(sa.cost_usd for sa in sub_answers)

    available = set(retrieved)
    dangling = sorted(set(citations) - available)

    return QaAnswer(
        question=question,
        answer=body,
        citations=citations,
        retrieved_paths=retrieved,
        dangling_citations=dangling,
        model=SINGLE_DEFAULT_MODEL,
        tokens_in=total_in,
        tokens_out=total_out,
        cost_usd=total_cost,
    )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "PROMPT_NAME",
    "SubQuery",
    "decompose_query",
    "qa_with_decomposition",
]
