"""``marginalia.lint_check`` — verify the wrapper passes through to lint_wiki.

We don't re-test the lint detectors themselves (covered by
``tests/test_lint.py``); we just confirm the tool surface returns a
structured ``LintReport`` against the canonical poc-wiki fixture, with
the LLM step stubbed so no API key is needed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from engine.tools.lint_check import LintReport, lint_check
from tests.conftest import StubAnthropicClient

POC_WIKI = Path(__file__).resolve().parents[2] / "notebooks" / "data" / "poc-wiki"


def test_lint_check_returns_lint_report_against_poc_wiki() -> None:
    """End-to-end: walk the poc-wiki, return a LintReport with structure intact.

    Stub Opus with an empty contradictions list — we're verifying the
    wrapper plumbing, not the contradiction prompt itself.
    """
    client = StubAnthropicClient(texts=["```json\n[]\n```"])
    report = lint_check(
        POC_WIKI,
        client=client,
        threshold_days=14,
        today=date(2026, 5, 3),
    )

    assert isinstance(report, LintReport)
    assert report.total_pages > 0
    # Stale-detection sanity: same fixture as test_lint.py guarantees ≥1 stale page
    # at this date+threshold.
    assert any(s.wikilink == "knowledge/decisions/apollo-q2-ship" for s in report.stale_pages)
    # Stub returned an empty contradictions array; the wrapper should pass it through.
    assert report.contradictions == []
