"""QA single-question path (design §7.1, behavior 1).

Retrieves relevant pages via ``marginalia.search``, reads their
contents, and asks Sonnet 4.6 for an answer with `[[wikilink]]`
citations. Citation integrity is verified against the retrieved set —
the model can't invent paths.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from engine.agents.synthesis.cross_source import extract_wikilinks
from engine.prompts import Prompt, load_prompt
from engine.tools.read_page import PageNotFoundError, read_page
from engine.tools.search import SearchHit, search
from engine.utils.api_compat import temperature_kwargs
from engine.utils.cost_tracker import estimate_cost_usd

if TYPE_CHECKING:
    from anthropic import Anthropic

PROMPT_NAME = "qa_single"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TOP_K = 5

_CITATIONS_RE = re.compile(r"^CITATIONS:\s*(.*)$", flags=re.MULTILINE)


class QaAnswer(BaseModel):
    """Final QA output — answer body + verified citations + cost."""

    model_config = ConfigDict(extra="forbid")

    question: str
    answer: str
    citations: list[str] = Field(default_factory=list)
    retrieved_paths: list[str] = Field(default_factory=list)
    dangling_citations: list[str] = Field(default_factory=list)
    model: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)


def _format_retrieved_block(hits: list[SearchHit], pages: list[tuple[str, str, str]]) -> str:
    """Render retrieved pages as <page path=...><title>...</title>...</page> blocks."""
    out: list[str] = []
    for hit, (path, title, body) in zip(hits, pages, strict=True):
        out.append(
            f'<page path="{path}" score="{hit.score:.3f}">\n<title>{title}</title>\n{body}\n</page>'
        )
    return "\n\n".join(out)


def _split_answer_and_citations(raw: str) -> tuple[str, list[str]]:
    match = _CITATIONS_RE.search(raw)
    if match:
        citations_str = match.group(1)
        cited = extract_wikilinks(citations_str)
        answer = raw[: match.start()].rstrip()
        return answer, cited
    # No CITATIONS line — fall back to body wikilinks only.
    return raw.strip(), extract_wikilinks(raw)


async def qa_single_question(
    question: str,
    *,
    wiki_root: Path,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    top_k: int = DEFAULT_TOP_K,
    prompt: Prompt | None = None,
    pre_retrieved: list[SearchHit] | None = None,
) -> QaAnswer:
    """Answer ``question`` using the wiki at ``wiki_root``.

    ``pre_retrieved`` lets callers (decomposition + gap-detection
    paths) supply hits they already computed. When ``None``, runs
    ``search(question, wiki_root, limit=top_k)`` itself.
    """
    hits = (
        pre_retrieved
        if pre_retrieved is not None
        else search(question, wiki_root=wiki_root, limit=top_k)
    )

    pages: list[tuple[str, str, str]] = []
    for hit in hits:
        try:
            page, body = read_page(hit.path, wiki_root=wiki_root)
        except PageNotFoundError:
            continue
        pages.append((hit.path, page.title, body))

    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    prompt = prompt or load_prompt(PROMPT_NAME)
    available_paths = [hit.path for hit in hits]
    available_paths_block = "\n".join(f"- {p}" for p in available_paths) or "(none)"

    if pages:
        user_msg = prompt.user_template.format(
            question=question,
            retrieved_pages_block=_format_retrieved_block(hits[: len(pages)], pages),
            available_paths_block=available_paths_block,
        )
    else:
        # No pages found — prompt the model to say so plainly.
        user_msg = prompt.user_template.format(
            question=question,
            retrieved_pages_block="(no pages retrieved)",
            available_paths_block="(no available paths)",
        )

    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        **temperature_kwargs(model),
        system=prompt.system,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = resp.content[0].text
    answer, citations = _split_answer_and_citations(raw)

    available_set = set(available_paths)
    dangling = sorted(set(citations) - available_set)
    cost = estimate_cost_usd(model, resp.usage.input_tokens, resp.usage.output_tokens)

    return QaAnswer(
        question=question,
        answer=answer,
        citations=citations,
        retrieved_paths=available_paths,
        dangling_citations=dangling,
        model=model,
        tokens_in=resp.usage.input_tokens,
        tokens_out=resp.usage.output_tokens,
        cost_usd=cost,
    )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_TOP_K",
    "PROMPT_NAME",
    "QaAnswer",
    "qa_single_question",
]
