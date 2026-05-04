"""``marginalia.lint_check`` — agent-callable tool over the lint pass (design §7.4).

Thin wrapper around ``engine.agents.lint.full_pass.lint_wiki`` so the
orchestrator can request a lint run as a tool call and reason over the
structured report without re-implementing the staleness / contradiction /
orphan logic. Parameter shape is the wikis-this-agent-knows-about minimum:

- ``wiki_root`` — required.
- ``client`` / ``model`` — optional; default to a fresh Anthropic client +
  Opus 4.7 (the contradiction step's chosen model).
- ``threshold_days`` — staleness cutoff; matches ``lint_wiki``'s default.

Returns the same ``LintReport`` Pydantic model the agent code uses, so
callers serialize it however they like (``model_dump_json`` for tool
results, ``format_lint_report_md`` for human output).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from engine.agents.lint.full_pass import (
    DEFAULT_MODEL,
    DEFAULT_STALE_THRESHOLD_DAYS,
    LintReport,
    lint_wiki,
)

if TYPE_CHECKING:
    from anthropic import Anthropic


def lint_check(
    wiki_root: Path | str,
    *,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
    today: date | None = None,
) -> LintReport:
    """Run a lint pass; return the structured report.

    Surfaces the same three findings the nightly job runs:

    - **Stale pages** (pure local; no LLM call).
    - **Orphans** (pure local; no LLM call).
    - **Contradictions** (Opus 4.7; the only LLM call).

    Pass an in-memory wiki fixture as ``wiki_root`` for tests; production
    callers pass ``$WIKI_CONTENT_REPO``.
    """
    return lint_wiki(
        Path(wiki_root),
        client=client,
        model=model,
        threshold_days=threshold_days,
        today=today,
    )


__all__ = ["LintReport", "lint_check"]
