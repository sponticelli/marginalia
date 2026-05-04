"""Lint agent: nightly contradiction + staleness + orphan scan (design §10 Scenario D)."""

from engine.agents.lint.full_pass import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_STALE_THRESHOLD_DAYS,
    PROMPT_NAME,
    Contradiction,
    LintReport,
    OrphanFinding,
    Severity,
    StaleFinding,
    detect_contradictions,
    detect_orphans,
    detect_stale,
    format_lint_report_md,
    format_pages_block,
    lint_wiki,
)

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
