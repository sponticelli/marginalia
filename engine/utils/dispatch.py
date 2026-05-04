"""Extension → adapter routing (design §8 dispatch table).

`extract` is the entry point: given a path, hand off to the right
adapter and return ``ExtractedContent``. Unknown extensions raise
``UnsupportedExtensionError`` rather than silently passing through —
silent fallback would mask adapter coverage gaps.

Three layers of API live here:

1. ``extract`` / ``extract_url`` — return ``ExtractedContent`` only.
   Used by the original ingest path that doesn't need to know the
   adapter name.
2. ``route_path`` / ``route_url`` — return only the adapter name
   (no I/O). Used by ingest to record provenance on ``SourceRef``.
3. ``dispatch_by_adapter`` — call a named adapter directly. Used by
   ``marginalia resync`` to re-run a page through the same adapter
   without re-deriving routing from the source ref.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from engine.adapters._template.contract import ExtractedContent
from engine.adapters.apple_notes.extractor import (
    NOTES_URL_SCHEME,
    extract_apple_note,
)
from engine.adapters.github.extractor import extract_github
from engine.adapters.gmail.extractor import extract_gmail
from engine.adapters.google_doc.extractor import extract_google_doc
from engine.adapters.local_fs.image import extract_image
from engine.adapters.local_fs.pdf import extract_pdf
from engine.adapters.notion.extractor import extract_notion
from engine.adapters.slack.extractor import extract_slack
from engine.adapters.web.extractor import extract_web
from engine.adapters.youtube.extractor import extract_youtube

if TYPE_CHECKING:
    from anthropic import Anthropic


Adapter = Literal[
    "local_fs_text",  # plain markdown / txt passthrough
    "local_fs_pdf",
    "local_fs_image",
    "youtube",
    "google_doc",
    "gmail",
    "notion",
    "slack",
    "github",
    "apple_notes",
    "web",
]
"""Adapter name carried on ``SourceRef.adapter`` for resync routing.

Each value corresponds to exactly one extractor function. The name
encodes "which family fetched this" — separate from
``ExtractedContent.extraction_method`` which encodes "how was the
text obtained" (text vs vision vs transcript). Two pages from
different adapters can share an extraction_method ("text" is common);
the adapter name disambiguates them for resync."""

_TEXT_EXTENSIONS = {".md", ".txt"}
_PDF_EXTENSIONS = {".pdf"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "music.youtube.com",
}
_GOOGLE_DOCS_HOSTS = {
    "docs.google.com",
    "www.docs.google.com",
}
_GMAIL_HOSTS = {
    "mail.google.com",
    "www.mail.google.com",
}
_NOTION_HOSTS = {
    "notion.so",
    "www.notion.so",
}
_GITHUB_HOSTS = {
    "github.com",
    "www.github.com",
}


class UnsupportedExtensionError(ValueError):
    """Raised when ``extract`` is asked about an unrouted extension."""


class UnsupportedUrlError(ValueError):
    """Raised when ``extract_url`` is asked about an unrouted URL."""


def _extract_text_passthrough(path: Path) -> ExtractedContent:
    return ExtractedContent(
        text=path.read_text(encoding="utf-8"),
        pages=None,
        extraction_method="text",
        chars_per_page=None,
        cost_usd=None,
    )


def extract(
    path: Path | str,
    *,
    client: Anthropic | None = None,
) -> ExtractedContent:
    """Route a local file to the right adapter by extension.

    The ``client`` is forwarded only to adapters that may make LLM
    calls; the trivial text passthrough never touches it. This keeps
    the most common case (markdown/txt) free.
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()

    if suffix in _TEXT_EXTENSIONS:
        return _extract_text_passthrough(file_path)
    if suffix in _PDF_EXTENSIONS:
        return extract_pdf(file_path, client=client)
    if suffix in _IMAGE_EXTENSIONS:
        return extract_image(file_path, client=client)

    raise UnsupportedExtensionError(
        f"no adapter registered for extension {suffix!r}; "
        f"supported: {sorted(_TEXT_EXTENSIONS | _PDF_EXTENSIONS | _IMAGE_EXTENSIONS)}"
    )


async def extract_url(
    url: str,
    *,
    client: Anthropic | None = None,
    summary_model: str | None = "claude-haiku-4-5",
) -> ExtractedContent:
    """Route a remote URL to the right adapter by URL pattern.

    Host-specific adapters take priority (e.g. YouTube → transcript).
    URLs without a registered host fall through to the generic ``web``
    adapter, which fetches the HTML and extracts main-content
    markdown. URLs with non-http schemes are still rejected with
    ``UnsupportedUrlError`` — the dispatcher only handles the open web.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme == NOTES_URL_SCHEME:
        return await extract_apple_note(url)
    if scheme not in ("http", "https"):
        raise UnsupportedUrlError(
            f"unsupported scheme {scheme!r}; only http(s) URLs are dispatched"
        )

    host = (parsed.hostname or "").lower()
    if host in _YOUTUBE_HOSTS:
        return await extract_youtube(url, client=client, summary_model=summary_model)
    if host in _GOOGLE_DOCS_HOSTS and "/document/d/" in (parsed.path or ""):
        return await extract_google_doc(url)
    if host in _GMAIL_HOSTS:
        return await extract_gmail(url)
    if host in _NOTION_HOSTS:
        return await extract_notion(url)
    if host.endswith(".slack.com") and "/archives/" in (parsed.path or ""):
        return await extract_slack(url)
    if host in _GITHUB_HOSTS:
        # Only routes the issue/PR/discussion paths; raw repo browsing
        # falls through to the web fallback (which renders github.com
        # README.md / file views perfectly fine).
        path = parsed.path or ""
        if "/issues/" in path or "/pull/" in path or "/discussions/" in path:
            return await extract_github(url)

    return await extract_web(url)


def route_path(path: Path | str) -> Adapter:
    """Return the adapter name that ``extract`` would invoke for ``path``.

    Pure dispatch decision — no I/O. Mirrors the if/elif chain in
    ``extract`` exactly. Raises the same exceptions on miss so resync
    can surface them with the same error class as the original ingest.
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in _TEXT_EXTENSIONS:
        return "local_fs_text"
    if suffix in _PDF_EXTENSIONS:
        return "local_fs_pdf"
    if suffix in _IMAGE_EXTENSIONS:
        return "local_fs_image"
    raise UnsupportedExtensionError(
        f"no adapter registered for extension {suffix!r}; "
        f"supported: {sorted(_TEXT_EXTENSIONS | _PDF_EXTENSIONS | _IMAGE_EXTENSIONS)}"
    )


def route_url(url: str) -> Adapter:
    """Return the adapter name that ``extract_url`` would invoke for ``url``.

    Pure dispatch decision — no I/O. Mirrors ``extract_url``'s routing
    table exactly. URLs with non-http(s) schemes besides ``notes://``
    raise ``UnsupportedUrlError``.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme == NOTES_URL_SCHEME:
        return "apple_notes"
    if scheme not in ("http", "https"):
        raise UnsupportedUrlError(
            f"unsupported scheme {scheme!r}; only http(s) URLs are dispatched"
        )

    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host in _YOUTUBE_HOSTS:
        return "youtube"
    if host in _GOOGLE_DOCS_HOSTS and "/document/d/" in path:
        return "google_doc"
    if host in _GMAIL_HOSTS:
        return "gmail"
    if host in _NOTION_HOSTS:
        return "notion"
    if host.endswith(".slack.com") and "/archives/" in path:
        return "slack"
    if host in _GITHUB_HOSTS and (
        "/issues/" in path or "/pull/" in path or "/discussions/" in path
    ):
        return "github"
    return "web"


async def dispatch_by_adapter(
    adapter: Adapter,
    ref: str,
    *,
    client: Anthropic | None = None,
) -> ExtractedContent:
    """Re-run a named adapter against a stored ``SourceRef.ref``.

    Used by ``marginalia resync`` so we don't re-derive the adapter
    from the ref each time — for ambiguous refs (Notion page IDs,
    bare doc IDs) the original dispatch decision may not be
    reproducible from the ref alone.
    """
    if adapter == "local_fs_text":
        return _extract_text_passthrough(Path(ref))
    if adapter == "local_fs_pdf":
        return extract_pdf(Path(ref), client=client)
    if adapter == "local_fs_image":
        return extract_image(Path(ref), client=client)
    if adapter == "youtube":
        return await extract_youtube(ref, client=client)
    if adapter == "google_doc":
        return await extract_google_doc(ref)
    if adapter == "gmail":
        return await extract_gmail(ref)
    if adapter == "notion":
        return await extract_notion(ref)
    if adapter == "slack":
        return await extract_slack(ref)
    if adapter == "github":
        return await extract_github(ref)
    if adapter == "apple_notes":
        return await extract_apple_note(ref)
    if adapter == "web":
        return await extract_web(ref)
    # mypy/exhaustiveness — every Adapter literal is handled above.
    raise ValueError(f"unknown adapter: {adapter!r}")


__all__ = [
    "Adapter",
    "UnsupportedExtensionError",
    "UnsupportedUrlError",
    "dispatch_by_adapter",
    "extract",
    "extract_url",
    "route_path",
    "route_url",
]
