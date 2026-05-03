"""QA gap-detection path (design §7.1, behavior 3 + Scenario I).

When retrieval is thin (fewer than ``min_pages`` hits, or top score
below ``min_score``), don't synthesize a weak answer. Return a
structured ``KnowledgeGap`` carrying suggested next ingests so the
curator can run ``marginalia add <suggested URL>`` and re-query.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from engine.agents.qa.single_question import QaAnswer, qa_single_question
from engine.prompts import Prompt, load_prompt
from engine.tools.search import SearchHit, search
from engine.utils.api_compat import temperature_kwargs
from engine.utils.cost_tracker import estimate_cost_usd

if TYPE_CHECKING:
    from anthropic import Anthropic

PROMPT_NAME = "qa_gap_signal"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 512
DEFAULT_MIN_PAGES = 2
DEFAULT_MIN_SCORE = 0.1


class KnowledgeGap(BaseModel):
    """Structured signal that the wiki can't answer this question yet."""

    model_config = ConfigDict(extra="forbid")

    query: str
    retrieved_paths: list[str] = Field(default_factory=list)
    retrieved_scores: list[float] = Field(default_factory=list)
    suggested_ingests: list[str] = Field(default_factory=list)
    reason: str
    cost_usd: float = Field(default=0.0, ge=0.0)


def _extract_json_object(raw: str) -> dict | None:
    text = raw.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _is_thin(hits: list[SearchHit], min_pages: int, min_score: float) -> bool:
    if len(hits) < min_pages:
        return True
    return bool(not hits or hits[0].score < min_score)


async def _build_gap(
    question: str,
    hits: list[SearchHit],
    *,
    client: Anthropic,
    model: str,
    max_tokens: int,
    prompt: Prompt,
) -> KnowledgeGap:
    retrieved_pages_block = (
        "\n\n".join(
            f'<page path="{h.path}" score="{h.score:.3f}">\n<title>{h.title}</title>\n{h.snippet}\n</page>'
            for h in hits
        )
        if hits
        else "(no hits)"
    )
    user_msg = prompt.user_template.format(
        question=question,
        retrieved_pages_block=retrieved_pages_block,
    )
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        **temperature_kwargs(model),
        system=prompt.system,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = resp.content[0].text
    cost = estimate_cost_usd(model, resp.usage.input_tokens, resp.usage.output_tokens)
    data = _extract_json_object(raw) or {}
    return KnowledgeGap(
        query=question,
        retrieved_paths=[h.path for h in hits],
        retrieved_scores=[h.score for h in hits],
        suggested_ingests=list(data.get("suggested_ingests", []) or []),
        reason=str(data.get("reason", "thin coverage")),
        cost_usd=cost,
    )


async def qa_with_gap_detection(
    question: str,
    *,
    wiki_root: Path,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    min_pages: int = DEFAULT_MIN_PAGES,
    min_score: float = DEFAULT_MIN_SCORE,
    top_k: int = 5,
    gap_prompt: Prompt | None = None,
    single_prompt: Prompt | None = None,
) -> QaAnswer | KnowledgeGap:
    """Search; if retrieval is thin, return a ``KnowledgeGap``; else answer normally."""
    hits = search(question, wiki_root=wiki_root, limit=top_k)

    if _is_thin(hits, min_pages, min_score):
        if client is None:
            from anthropic import Anthropic as _Anthropic

            client = _Anthropic()
        gap_prompt = gap_prompt or load_prompt(PROMPT_NAME)
        return await _build_gap(
            question,
            hits,
            client=client,
            model=model,
            max_tokens=max_tokens,
            prompt=gap_prompt,
        )

    return await qa_single_question(
        question,
        wiki_root=wiki_root,
        client=client,
        top_k=top_k,
        pre_retrieved=hits,
        prompt=single_prompt,
    )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MIN_PAGES",
    "DEFAULT_MIN_SCORE",
    "DEFAULT_MODEL",
    "PROMPT_NAME",
    "KnowledgeGap",
    "qa_with_gap_detection",
]
