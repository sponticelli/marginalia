"""Web-page source adapter (design §8 web row).

Fetches an arbitrary HTTP/HTTPS URL and extracts the main content as
markdown. Built on ``trafilatura`` because it ships best-in-class
boilerplate-removal heuristics out of the box (Mozilla Readability is
the next-best baseline; trafilatura beats it on benchmark corpora and
already returns markdown directly).

Failure modes are *structured*, mirroring the YouTube adapter: fetch
errors, parse failures, and below-threshold content all return an
``ExtractedContent`` with ``failure_reason`` populated and
``text=""``. Callers can collect partial success across a batch
instead of catching exceptions per URL.

Caching: raw HTML responses are cached under
``notebooks/data/transcripts/web/<host>/<sha256>.html`` so re-running
ingest on the same URL is offline-reproducible (matches the
``transcripts/`` sibling convention CLAUDE.md spells out for
fixture-cached upstream data).
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import urlparse

from engine.adapters._template.contract import ExtractedContent

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; marginalia/0.1; +https://github.com/sandroponticelli/marginalia)"
)
# Trafilatura's default minimum extracted-text length to consider a page
# "useful." Below this threshold we return failure_reason rather than a
# stub markdown — saves the analyze step from running on a paywall.
MIN_USEFUL_CHARS = 200


class WebFetchError(ValueError):
    """Raised when a URL is malformed or not http(s)."""


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebFetchError(f"unsupported scheme {parsed.scheme!r}: {url!r}")
    if not parsed.netloc:
        raise WebFetchError(f"missing host: {url!r}")


def _cache_path(url: str, cache_root: Path) -> Path:
    """Deterministic ``<host>/<sha256>.html`` path under ``cache_root``."""
    parsed = urlparse(url)
    host = (parsed.hostname or "_").replace(":", "_")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_root / host / f"{digest}.html"


def _fetch_html(
    url: str,
    *,
    user_agent: str,
    cache_root: Path | None,
) -> str | None:
    """Fetch ``url`` to HTML; consult ``cache_root`` first if provided.

    Returns the HTML body on success, ``None`` on any fetch failure.
    Trafilatura's ``fetch_url`` honors the supplied UA and handles
    encoding detection — preferred over rolling our own with urllib.
    """
    if cache_root is not None:
        cached = _cache_path(url, cache_root)
        if cached.is_file():
            return cached.read_text(encoding="utf-8")

    from trafilatura import fetch_url
    from trafilatura.settings import use_config

    cfg = use_config()
    cfg.set("DEFAULT", "USER_AGENTS", user_agent)
    html = fetch_url(url, config=cfg)
    if html is None:
        return None

    if cache_root is not None:
        cached = _cache_path(url, cache_root)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(html, encoding="utf-8")

    return html


def _html_to_markdown(html: str, *, source_url: str) -> tuple[str | None, str | None]:
    """Run trafilatura's main-content extraction → (markdown, title).

    Both return values may be ``None`` if trafilatura couldn't find a
    main-content region (e.g. a JS-rendered SPA shell with no
    server-side text).
    """
    from trafilatura import extract, extract_metadata

    markdown = extract(
        html,
        url=source_url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        include_comments=False,
        favor_recall=False,
    )
    metadata = extract_metadata(html, default_url=source_url)
    title = metadata.title if metadata is not None else None
    return markdown, title


async def extract_web(
    url: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    cache_root: Path | None = None,
    min_useful_chars: int = MIN_USEFUL_CHARS,
    cached_html: str | None = None,
) -> ExtractedContent:
    """Fetch ``url`` and return its main content as markdown.

    Pipeline:

    1. Validate the URL is http(s) — anything else raises
       ``WebFetchError`` (for adapter-internal use; the dispatcher
       should never call us with non-http URLs).
    2. Fetch the HTML (cache hit if a recorded fixture exists under
       ``cache_root``).
    3. Run trafilatura's main-content extraction → markdown + title.
    4. Below ``min_useful_chars`` → graceful failure with reason.

    ``cached_html`` lets callers (notebook fixtures, tests) bypass the
    network entirely. When provided, the extractor uses it verbatim
    and skips both the fetch and the cache lookup.
    """
    try:
        _validate_url(url)
    except WebFetchError as exc:
        return _failure(f"invalid_url: {exc}")

    if cached_html is not None:
        html = cached_html
    else:
        html = await asyncio.to_thread(
            _fetch_html, url, user_agent=user_agent, cache_root=cache_root
        )
    if not html:
        return _failure("fetch_failed: no HTML returned (network error or 4xx/5xx)")

    markdown, title = await asyncio.to_thread(_html_to_markdown, html, source_url=url)
    if not markdown or len(markdown) < min_useful_chars:
        # Trafilatura returned nothing useful — a paywall, JS-only SPA,
        # or empty page. We surface the reason so the caller can decide
        # whether to fall back (e.g. screenshot via vision).
        actual = len(markdown) if markdown else 0
        return _failure(f"insufficient_content: got {actual} chars (min {min_useful_chars})")

    body = f"# {title}\n\nSource: {url}\n\n{markdown}" if title else f"Source: {url}\n\n{markdown}"

    return ExtractedContent(
        text=body,
        pages=None,
        extraction_method="text",
        cost_usd=None,
        failure_reason=None,
    )


def _failure(reason: str) -> ExtractedContent:
    return ExtractedContent(
        text="",
        pages=None,
        extraction_method="text",
        failure_reason=reason,
    )


__all__ = [
    "DEFAULT_USER_AGENT",
    "MIN_USEFUL_CHARS",
    "WebFetchError",
    "extract_web",
]
