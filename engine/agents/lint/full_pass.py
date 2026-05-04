"""Lint pass: staleness + contradictions + orphans (design §10 Scenario D).

The lint agent runs as a nightly batch job over the whole wiki. Three
findings, one report:

- **Staleness** — purely local: ``(today - last_synced).days >= threshold``.
- **Contradictions** — Opus 4.7 with required CoT, scoped to decisions
  and concepts (sources are facts; entities are hubs). The cert-valuable
  cell.
- **Orphans** — a wiki page with zero inbound references anywhere in
  the corpus. Pure Python; uses the same wikilink scan the rewriter
  does.

``lint_wiki`` composes the three and returns a ``LintReport`` that
``format_lint_report_md`` can render to disk.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from engine.models.pages import PageType
from engine.prompts import Prompt, load_prompt
from engine.utils.api_compat import temperature_kwargs
from engine.utils.backlink_rewrite import all_referenced_wikilinks
from engine.utils.cost_tracker import estimate_cost_usd
from engine.utils.wiki_walker import WikiPage, walk_wiki

if TYPE_CHECKING:
    from anthropic import Anthropic

PROMPT_NAME = "lint"
DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_STALE_THRESHOLD_DAYS = 14

_CONTRADICTION_TYPES = (PageType.DECISION, PageType.CONCEPT)
_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)\s*```", flags=re.DOTALL)


Severity = Literal["low", "medium", "high"]


class StaleFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wikilink: str
    page_type: PageType
    last_synced: date
    days_stale: int = Field(ge=0)


class Contradiction(BaseModel):
    """One pair of contradicting pages, as judged by Opus."""

    model_config = ConfigDict(extra="forbid")

    page_a: str
    page_b: str
    subject: str
    why: str
    severity: Severity
    evidence: list[str] = Field(default_factory=list)


class OrphanFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wikilink: str
    page_type: PageType


class LintReport(BaseModel):
    """Aggregate findings from one lint pass."""

    model_config = ConfigDict(extra="forbid")

    total_pages: int = Field(ge=0)
    stale_pages: list[StaleFinding] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    orphans: list[OrphanFinding] = Field(default_factory=list)
    model: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    wall_seconds: float = Field(ge=0.0)


def detect_stale(
    pages: list[WikiPage],
    *,
    threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
    today: date | None = None,
) -> list[StaleFinding]:
    """Pages whose ``last_synced`` is older than ``threshold_days`` days."""
    today = today or date.today()
    out: list[StaleFinding] = []
    for page in pages:
        last = page.page.last_synced
        delta = (today - last).days
        if delta >= threshold_days:
            out.append(
                StaleFinding(
                    wikilink=page.wikilink,
                    page_type=page.page_type,
                    last_synced=last,
                    days_stale=delta,
                )
            )
    out.sort(key=lambda f: f.days_stale, reverse=True)
    return out


def detect_orphans(pages: list[WikiPage]) -> list[OrphanFinding]:
    """Pages with zero inbound references anywhere else in the corpus."""
    referenced = all_referenced_wikilinks(pages)
    out: list[OrphanFinding] = []
    for page in pages:
        if page.wikilink in referenced:
            continue
        out.append(OrphanFinding(wikilink=page.wikilink, page_type=page.page_type))
    out.sort(key=lambda f: f.wikilink)
    return out


def format_pages_block(pages: list[WikiPage]) -> str:
    """Render decisions+concepts as ``<page>...</page>`` XML for the lint prompt."""
    blocks: list[str] = []
    for page in pages:
        if page.page_type not in _CONTRADICTION_TYPES:
            continue
        blocks.append(
            f'<page id="{page.wikilink}" type="{page.page_type.value}" '
            f'status="{page.page.status.value}" '
            f'last_synced="{page.page.last_synced.isoformat()}">\n'
            f"<title>{page.page.title}</title>\n"
            f"{page.body.strip()}\n"
            f"</page>"
        )
    return "\n\n".join(blocks)


def _extract_json_array(raw: str) -> list[dict] | None:
    """Pull the first ```json [...] ``` block out of a model response."""
    match = _JSON_BLOCK_RE.search(raw)
    if match:
        candidate = match.group(1)
    else:
        bracket = re.search(r"\[.*\]", raw, flags=re.DOTALL)
        if not bracket:
            return None
        candidate = bracket.group(0)

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def detect_contradictions(
    pages: list[WikiPage],
    *,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    prompt: Prompt | None = None,
) -> tuple[list[Contradiction], int, int]:
    """Run Opus over decisions+concepts, return validated contradictions + usage.

    Returns ``(contradictions, tokens_in, tokens_out)``. Bad JSON or an
    empty list means "no contradictions found." Per-contradiction
    validation failures are dropped (lint-level lenience: the model
    sometimes adds extra fields).
    """
    candidate_pages = [p for p in pages if p.page_type in _CONTRADICTION_TYPES]
    if len(candidate_pages) < 2:
        return [], 0, 0

    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    prompt = prompt or load_prompt(PROMPT_NAME)
    user_msg = prompt.user_template.format(pages_block=format_pages_block(candidate_pages))

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
        return [], resp.usage.input_tokens, resp.usage.output_tokens

    contradictions: list[Contradiction] = []
    for item in data:
        try:
            contradictions.append(Contradiction.model_validate(item))
        except ValidationError:
            continue

    return contradictions, resp.usage.input_tokens, resp.usage.output_tokens


def lint_wiki(
    wiki_root: Path,
    *,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    today: date | None = None,
    threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
    prompt: Prompt | None = None,
) -> LintReport:
    """Full lint pass: staleness + contradictions + orphans → ``LintReport``.

    Walks ``wiki_root`` once; passes the page list to each detector in
    turn. The Opus call is the only LLM call and the only network I/O.
    """
    pages = list(walk_wiki(wiki_root))
    started = time.perf_counter()

    stale = detect_stale(pages, threshold_days=threshold_days, today=today)
    orphans = detect_orphans(pages)
    contradictions, tokens_in, tokens_out = detect_contradictions(
        pages, client=client, model=model, prompt=prompt
    )

    cost = estimate_cost_usd(model, tokens_in, tokens_out)
    wall = time.perf_counter() - started

    return LintReport(
        total_pages=len(pages),
        stale_pages=stale,
        contradictions=contradictions,
        orphans=orphans,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        wall_seconds=wall,
    )


def format_lint_report_md(report: LintReport, *, generated_on: date | None = None) -> str:
    """Render ``LintReport`` as the markdown text written to ``lint-report.md``."""
    generated_on = generated_on or date.today()
    lines: list[str] = []
    lines.append("# Lint report")
    lines.append("")
    lines.append(f"**Generated:** {generated_on.isoformat()}")
    lines.append(f"**Total pages walked:** {report.total_pages}")
    lines.append(f"**Model:** `{report.model}`")
    lines.append(
        f"**Cost:** ${report.cost_usd:.6f} "
        f"({report.tokens_in} in / {report.tokens_out} out tokens, "
        f"{report.wall_seconds:.2f}s wall)"
    )
    lines.append("")

    lines.append(f"## Stale pages ({len(report.stale_pages)})")
    if report.stale_pages:
        lines.append("")
        lines.append("| Wikilink | Type | Last synced | Days stale |")
        lines.append("|---|---|---|---|")
        for s in report.stale_pages:
            lines.append(
                f"| `{s.wikilink}` | {s.page_type.value} | "
                f"{s.last_synced.isoformat()} | {s.days_stale} |"
            )
    else:
        lines.append("")
        lines.append("None.")
    lines.append("")

    lines.append(f"## Contradictions found ({len(report.contradictions)})")
    if report.contradictions:
        for i, c in enumerate(report.contradictions, 1):
            lines.append("")
            lines.append(f"### {i}. {c.subject} ({c.severity})")
            lines.append("")
            lines.append(f"- **Page A:** `{c.page_a}`")
            lines.append(f"- **Page B:** `{c.page_b}`")
            lines.append(f"- **Why:** {c.why}")
            if c.evidence:
                lines.append("- **Evidence:**")
                for e in c.evidence:
                    lines.append(f"  - {e!r}")
    else:
        lines.append("")
        lines.append("None.")
    lines.append("")

    lines.append(f"## Orphan pages ({len(report.orphans)})")
    if report.orphans:
        lines.append("")
        lines.append("| Wikilink | Type |")
        lines.append("|---|---|")
        for o in report.orphans:
            lines.append(f"| `{o.wikilink}` | {o.page_type.value} |")
    else:
        lines.append("")
        lines.append("None.")
    lines.append("")

    return "\n".join(lines)


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_STALE_THRESHOLD_DAYS",
    "PROMPT_NAME",
    "Contradiction",
    "LintReport",
    "OrphanFinding",
    "Severity",
    "StaleFinding",
    "detect_contradictions",
    "detect_orphans",
    "detect_stale",
    "format_lint_report_md",
    "format_pages_block",
    "lint_wiki",
]
