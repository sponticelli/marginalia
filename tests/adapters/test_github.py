"""github adapter — issue/PR/discussion fetch via the gh CLI.

Tests use the ``fetcher`` injection point to substitute hand-rolled
payloads for the gh subprocess. This is the right test surface
because the adapter's job is rendering, not subprocess control —
``open_pr`` already exercises the subprocess discipline.
"""

from __future__ import annotations

import pytest

from engine.adapters.github.extractor import (
    GitHubExtractError,
    GitHubRef,
    extract_github,
    parse_github_url,
)

# ─── parse_github_url ───────────────────────────────────────────────


def test_parse_url_issue() -> None:
    ref = parse_github_url("https://github.com/anthropics/claude-code/issues/123")
    assert ref == GitHubRef(owner="anthropics", repo="claude-code", kind="issue", number=123)


def test_parse_url_pr() -> None:
    ref = parse_github_url("https://github.com/anthropics/claude-code/pull/456")
    assert ref == GitHubRef(owner="anthropics", repo="claude-code", kind="pr", number=456)


def test_parse_url_discussion() -> None:
    ref = parse_github_url("https://github.com/anthropics/claude-code/discussions/7")
    assert ref == GitHubRef(owner="anthropics", repo="claude-code", kind="discussion", number=7)


def test_parse_shorthand() -> None:
    ref = parse_github_url("gh:anthropics/claude-code#42")
    assert ref == GitHubRef(owner="anthropics", repo="claude-code", kind="issue", number=42)


def test_parse_rejects_non_github_url() -> None:
    with pytest.raises(GitHubExtractError):
        parse_github_url("https://example.com/anthropics/claude-code/issues/1")


# ─── extract_github (with injected fetcher) ─────────────────────────


@pytest.mark.asyncio
async def test_extract_github_renders_issue_with_comments() -> None:
    """An issue with body + 2 comments → markdown with all parts visible."""
    payload = {
        "number": 123,
        "title": "Apollo launch blocked by auth bug",
        "body": "We're seeing 401s on the new auth path.",
        "author": {"login": "alice"},
        "createdAt": "2026-04-15T10:00:00Z",
        "state": "OPEN",
        "labels": [{"name": "bug"}, {"name": "blocker"}],
        "comments": [
            {
                "author": {"login": "bob"},
                "createdAt": "2026-04-15T11:00:00Z",
                "body": "Reproduced on staging.",
            },
            {
                "author": {"login": "alice"},
                "createdAt": "2026-04-15T12:00:00Z",
                "body": "Fixed in #124.",
            },
        ],
    }
    result = await extract_github(
        "https://github.com/anthropics/claude-code/issues/123",
        fetcher=lambda ref: payload,
    )

    assert result.failure_reason is None
    assert "ISSUE #123" in result.text
    assert "Apollo launch blocked" in result.text
    assert "@alice" in result.text  # body author
    assert "@bob" in result.text  # comment author
    assert "Reproduced on staging" in result.text
    assert "Fixed in #124" in result.text
    assert "Labels: bug, blocker" in result.text


@pytest.mark.asyncio
async def test_extract_github_renders_pr_with_reviews() -> None:
    """A PR with reviews → reviews section in addition to comments."""
    payload = {
        "number": 456,
        "title": "Add lint dispatcher",
        "body": "Implements §10 D.",
        "author": {"login": "alice"},
        "createdAt": "2026-04-20T09:00:00Z",
        "state": "MERGED",
        "labels": [],
        "comments": [],
        "reviews": [
            {
                "author": {"login": "bob"},
                "submittedAt": "2026-04-20T10:00:00Z",
                "state": "APPROVED",
                "body": "LGTM",
            },
        ],
    }
    result = await extract_github(
        "https://github.com/anthropics/claude-code/pull/456",
        fetcher=lambda ref: payload,
    )
    assert "PR #456" in result.text
    assert "Reviews (1)" in result.text
    assert "review by @bob" in result.text
    assert "state: APPROVED" in result.text


@pytest.mark.asyncio
async def test_extract_github_invalid_url_no_fetcher_call() -> None:
    """Garbage input → graceful failure without invoking the fetcher."""

    def _fail(ref):
        raise AssertionError("fetcher should not be called")

    result = await extract_github("not a url", fetcher=_fail)
    assert result.failure_reason is not None
    assert "invalid_url" in result.failure_reason


@pytest.mark.asyncio
async def test_extract_github_handles_gh_failure() -> None:
    """gh subprocess raising RuntimeError → gh_failed graceful failure."""

    def _raise(ref):
        raise RuntimeError("gh issue view failed (exit 1): not authenticated")

    result = await extract_github(
        "https://github.com/anthropics/claude-code/issues/1",
        fetcher=_raise,
    )
    assert result.failure_reason is not None
    assert "gh_failed" in result.failure_reason
    assert "not authenticated" in result.failure_reason


@pytest.mark.asyncio
async def test_extract_github_handles_empty_response() -> None:
    """Empty payload → empty_response graceful failure."""
    result = await extract_github(
        "https://github.com/anthropics/claude-code/issues/1",
        fetcher=lambda ref: {},
    )
    assert result.failure_reason == "empty_response"


@pytest.mark.asyncio
async def test_dispatch_routes_github_issue_url(monkeypatch) -> None:
    """A github.com/.../issues/ URL routes to extract_github."""
    from engine.adapters._template.contract import ExtractedContent
    from engine.utils.dispatch import extract_url

    captured: list[str] = []

    async def _fake_gh(url, **_kw):
        captured.append(url)
        return ExtractedContent(text="ok", extraction_method="text")

    async def _fail_web(url, **_kw):
        raise AssertionError("web fallback called")

    import engine.utils.dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "extract_github", _fake_gh)
    monkeypatch.setattr(dispatch_module, "extract_web", _fail_web)

    url = "https://github.com/anthropics/claude-code/issues/123"
    result = await extract_url(url)
    assert result.text == "ok"
    assert captured == [url]


@pytest.mark.asyncio
async def test_dispatch_falls_through_to_web_for_repo_root(monkeypatch) -> None:
    """A repo-root github.com URL falls through to the web adapter (renders the README)."""
    from engine.adapters._template.contract import ExtractedContent
    from engine.utils.dispatch import extract_url

    captured: list[str] = []

    async def _fake_web(url, **_kw):
        captured.append(url)
        return ExtractedContent(text="repo readme", extraction_method="text")

    async def _fail_gh(url, **_kw):
        raise AssertionError("github adapter called for repo root")

    import engine.utils.dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "extract_web", _fake_web)
    monkeypatch.setattr(dispatch_module, "extract_github", _fail_gh)

    url = "https://github.com/anthropics/claude-code"
    result = await extract_url(url)
    assert result.text == "repo readme"
    assert captured == [url]
