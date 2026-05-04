"""Scaffold agent — keeps wiki meta-pages in sync with content (design §7.1).

Three targets, each with a different write contract:

- ``index`` — fully derivable from page state. Regenerates
  ``<wiki>/index.md`` directly; ``write=True`` is the default.
- ``purpose`` — human-authored. The agent *proposes* refinements
  based on what the wiki actually contains versus what the file
  claims; output lands in ``<wiki>/purpose.md.proposed`` for review.
  Pass ``write=True`` (CLI: ``--apply``) to overwrite the real file.
- ``agents`` — same recommend-then-confirm contract as ``purpose``,
  surfaces terminology drift between the style guide and recent pages.

The default-off-write semantics for purpose+agents exists so the
agent never silently rewrites the user's voice. The proposal carries
inline `<!-- DRIFT: ... -->` / `<!-- EVIDENCE: ... -->` comments
anchoring each suggestion to the wiki state that motivated it,
making the diff reviewable rather than a black-box rewrite.
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
PURPOSE_PROMPT_NAME = "scaffold_purpose"
AGENTS_PROMPT_NAME = "scaffold_agents"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096
INDEX_FILENAME = "index.md"
PURPOSE_FILENAME = "purpose.md"
AGENTS_FILENAME = "AGENTS.md"
PROPOSED_SUFFIX = ".proposed"
# How many pages to sample for the AGENTS.md prompt's body excerpt
# section — enough to surface terminology drift, few enough to keep
# the prompt under the model's working comfort zone.
AGENTS_SAMPLE_LIMIT = 12
AGENTS_EXCERPT_CHARS = 600

ScaffoldTarget = Literal["index", "purpose", "agents"]
SUPPORTED_TARGETS: tuple[ScaffoldTarget, ...] = ("index", "purpose", "agents")

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


def _excerpt_pages(pages: list[WikiPage], *, limit: int, chars: int) -> str:
    """Sample up to ``limit`` page bodies, truncated to ``chars`` each.

    Used by ``scaffold_agents`` so the LLM can spot terminology drift
    in real prose. Sorted by ``last_synced`` descending so the most
    recent pages — most likely to carry emerging vocabulary — go first.
    """
    by_recency = sorted(pages, key=lambda p: p.page.last_synced, reverse=True)[:limit]
    blocks: list[str] = []
    for p in by_recency:
        snippet = p.body.strip().replace("\n", " ")[:chars]
        blocks.append(f'<page path="{p.wikilink}">\n{snippet}\n</page>')
    return "\n\n".join(blocks)


def _llm_propose(
    *,
    prompt_name: str,
    user_msg: str,
    client: Anthropic | None,
    prompt: Prompt | None,
    model: str | None,
    max_tokens: int,
    on_cost: Callable[[CostRecord], None] | None,
    agent_label: str,
) -> tuple[str, str, int, int, float, float]:
    """Run one Sonnet call; return (content, model, tokens_in, tokens_out, cost, wall).

    Factored out because ``scaffold_purpose`` and ``scaffold_agents``
    differ only in prompt + user payload — the LLM-call shape is
    identical and worth sharing.
    """
    prompt = prompt or load_prompt(prompt_name)
    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    chosen_model = model or prompt.model or DEFAULT_MODEL

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
                agent=agent_label,
                model=chosen_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cached=False,
            )
        )

    return content, chosen_model, tokens_in, tokens_out, cost, wall


def _resolve_proposed_path(target_path: Path, *, write: bool) -> Path:
    """Pick the output path: real file when ``write=True``, ``.proposed`` otherwise."""
    return target_path if write else target_path.with_suffix(target_path.suffix + PROPOSED_SUFFIX)


async def scaffold_purpose(
    wiki_root: Path,
    *,
    config: MarginaliaConfig,
    client: Anthropic | None = None,
    prompt: Prompt | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    write: bool = False,
    on_cost: Callable[[CostRecord], None] | None = None,
) -> ScaffoldResult:
    """Propose refinements to ``<wiki_root>/purpose.md`` based on actual page state.

    Default behavior writes to ``purpose.md.proposed`` so the user can
    diff against the canonical file before deciding what to keep.
    Pass ``write=True`` (CLI: ``--apply``) to overwrite the real file
    — only do this after reviewing.
    """
    pages = list(walk_wiki(wiki_root))
    listing = format_pages_listing(pages)
    user_msg = load_prompt(PURPOSE_PROMPT_NAME).user_template.format(
        current_purpose=config.purpose_body,
        pages_listing=listing or "(no pages)",
    )

    content, chosen_model, tokens_in, tokens_out, cost, wall = _llm_propose(
        prompt_name=PURPOSE_PROMPT_NAME,
        user_msg=user_msg,
        client=client,
        prompt=prompt,
        model=model,
        max_tokens=max_tokens,
        on_cost=on_cost,
        agent_label="scaffold",
    )

    target_path = wiki_root / PURPOSE_FILENAME
    out_path = _resolve_proposed_path(target_path, write=write)
    out_path.write_text(content, encoding="utf-8")

    return ScaffoldResult(
        target="purpose",
        out_path=out_path,
        content=content,
        pages_indexed=len(pages),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        wall_seconds=wall,
        model=chosen_model,
    )


async def scaffold_agents(
    wiki_root: Path,
    *,
    config: MarginaliaConfig,
    client: Anthropic | None = None,
    prompt: Prompt | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    write: bool = False,
    sample_limit: int = AGENTS_SAMPLE_LIMIT,
    excerpt_chars: int = AGENTS_EXCERPT_CHARS,
    on_cost: Callable[[CostRecord], None] | None = None,
) -> ScaffoldResult:
    """Propose refinements to ``<wiki_root>/AGENTS.md`` based on terminology drift.

    Same recommend-then-confirm contract as ``scaffold_purpose``:
    writes to ``AGENTS.md.proposed`` by default; ``write=True`` is
    the explicit overwrite. Sends a sample of recent page bodies to
    the model so it can spot vocabulary drift the listing alone
    can't surface.
    """
    pages = list(walk_wiki(wiki_root))
    listing = format_pages_listing(pages)
    excerpts = _excerpt_pages(pages, limit=sample_limit, chars=excerpt_chars)
    user_msg = load_prompt(AGENTS_PROMPT_NAME).user_template.format(
        current_agents=config.agents_body,
        pages_listing=listing or "(no pages)",
        page_excerpts=excerpts or "(no pages)",
    )

    content, chosen_model, tokens_in, tokens_out, cost, wall = _llm_propose(
        prompt_name=AGENTS_PROMPT_NAME,
        user_msg=user_msg,
        client=client,
        prompt=prompt,
        model=model,
        max_tokens=max_tokens,
        on_cost=on_cost,
        agent_label="scaffold",
    )

    target_path = wiki_root / AGENTS_FILENAME
    out_path = _resolve_proposed_path(target_path, write=write)
    out_path.write_text(content, encoding="utf-8")

    return ScaffoldResult(
        target="agents",
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
    "AGENTS_FILENAME",
    "AGENTS_PROMPT_NAME",
    "AGENTS_SAMPLE_LIMIT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "INDEX_FILENAME",
    "INDEX_PROMPT_NAME",
    "PROPOSED_SUFFIX",
    "PURPOSE_FILENAME",
    "PURPOSE_PROMPT_NAME",
    "SUPPORTED_TARGETS",
    "ScaffoldResult",
    "ScaffoldTarget",
    "format_pages_listing",
    "scaffold_agents",
    "scaffold_index",
    "scaffold_purpose",
]
