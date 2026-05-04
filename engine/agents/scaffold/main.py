"""Scaffold agent — keeps wiki meta-pages in sync with content (design §7.1).

Today: one target, ``index``, which regenerates ``<wiki>/index.md``
from the current page tree. The other meta-pages the design references
(``purpose.md``, ``AGENTS.md``) are human-authored and should not be
silently overwritten — they're TODO follow-ups for a separate
"recommend updates" mode that diffs proposed against current and asks
the user before writing.

The agent's value over a hand-rolled markdown generator is grouping
decisions and short per-entry descriptions: with 50+ pages, a flat
list is unreadable but mechanical bucketing-by-type misses the
cross-cutting "what's *important* in this wiki" context the agent can
infer from titles and relationships.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.models.pages import PageStatus, PageType
from engine.prompts import Prompt, load_prompt
from engine.utils.api_compat import temperature_kwargs
from engine.utils.cost_tracker import CostRecord, estimate_cost_usd, record_attempt
from engine.utils.wiki_walker import WikiPage, walk_wiki

if TYPE_CHECKING:
    from anthropic import Anthropic

    from engine.models.wiki_config import MarginaliaConfig

INDEX_PROMPT_NAME = "scaffold_index"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096
INDEX_FILENAME = "index.md"

ScaffoldTarget = Literal["index", "purpose", "agents"]
SUPPORTED_TARGETS: tuple[ScaffoldTarget, ...] = ("index",)

# Order pages by type in the rendered listing — matches the prompt's
# expected section order so the LLM has fewer reordering decisions to make.
_TYPE_ORDER: tuple[PageType, ...] = (
    PageType.ENTITY,
    PageType.CONCEPT,
    PageType.DECISION,
    PageType.SOURCE,
    PageType.MEETING,
    PageType.METRIC,
    PageType.QA,
    PageType.ANALYSIS,
)


class ScaffoldResult(BaseModel):
    """Outcome of one scaffold run."""

    model_config = ConfigDict(extra="forbid")

    target: ScaffoldTarget
    out_path: Path
    content: str
    pages_indexed: int = Field(ge=0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    wall_seconds: float = Field(default=0.0, ge=0.0)
    model: str


def format_pages_listing(pages: list[WikiPage]) -> str:
    """Render the page tree as the structured input the prompt expects.

    Sorted by ``(type-order, title)`` so the LLM sees a stable layout
    and can focus on phrasing rather than reordering. Archived pages
    are tagged with their status so the prompt can route them to the
    footer section without a separate field.
    """
    type_index = {t: i for i, t in enumerate(_TYPE_ORDER)}
    sorted_pages = sorted(
        pages,
        key=lambda p: (type_index.get(p.page_type, len(type_index)), p.page.title.lower()),
    )
    lines: list[str] = []
    for p in sorted_pages:
        status_tag = (
            f" [status={p.page.status.value}]" if p.page.status != PageStatus.ACTIVE else ""
        )
        lines.append(
            f"- type={p.page_type.value} wikilink={p.wikilink} title={p.page.title!r}{status_tag}"
        )
    return "\n".join(lines)


def _purpose_excerpt(config: MarginaliaConfig, *, max_chars: int = 800) -> str:
    """Trim the wiki's purpose body to the first ~800 chars for prompt context."""
    body = config.purpose_body.strip()
    if len(body) <= max_chars:
        return body
    return body[:max_chars].rsplit("\n", 1)[0] + "\n…"


def _wiki_title(config: MarginaliaConfig) -> str:
    """Pull the H1 from purpose.md, or fall back to the wiki root's basename."""
    for line in config.purpose_body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return config.wiki_root.name or "Wiki"


async def scaffold_index(
    wiki_root: Path,
    *,
    config: MarginaliaConfig,
    client: Anthropic | None = None,
    prompt: Prompt | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    write: bool = True,
    on_cost: Callable[[CostRecord], None] | None = None,
) -> ScaffoldResult:
    """Generate (and optionally write) ``<wiki_root>/index.md`` from page state.

    The generated file overwrites any existing index.md — by design,
    this is the regeneration target. Pass ``write=False`` for a dry
    run that returns the proposed content without touching disk.
    """
    prompt = prompt or load_prompt(INDEX_PROMPT_NAME)
    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    pages = list(walk_wiki(wiki_root))
    listing = format_pages_listing(pages)
    chosen_model = model or prompt.model or DEFAULT_MODEL

    user_msg = prompt.user_template.format(
        wiki_title=_wiki_title(config),
        purpose_excerpt=_purpose_excerpt(config),
        pages_listing=listing or "(no pages)",
    )

    t0 = time.perf_counter()
    resp = client.messages.create(
        model=chosen_model,
        max_tokens=max_tokens,
        **temperature_kwargs(chosen_model),
        system=prompt.system,
        messages=[{"role": "user", "content": user_msg}],
    )
    wall = time.perf_counter() - t0

    content = resp.content[0].text.strip() + "\n"
    tokens_in = int(getattr(resp.usage, "input_tokens", 0) or 0)
    tokens_out = int(getattr(resp.usage, "output_tokens", 0) or 0)
    cost = estimate_cost_usd(chosen_model, tokens_in, tokens_out)

    if on_cost is not None:
        on_cost(
            record_attempt(
                agent="scaffold",
                model=chosen_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cached=False,
            )
        )

    out_path = wiki_root / INDEX_FILENAME
    if write:
        out_path.write_text(content, encoding="utf-8")

    return ScaffoldResult(
        target="index",
        out_path=out_path,
        content=content,
        pages_indexed=len(pages),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        wall_seconds=wall,
        model=chosen_model,
    )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "INDEX_FILENAME",
    "INDEX_PROMPT_NAME",
    "SUPPORTED_TARGETS",
    "ScaffoldResult",
    "ScaffoldTarget",
    "format_pages_listing",
    "scaffold_index",
]
