"""Extension → adapter routing (design §8 dispatch table).

`extract` is the entry point: given a path, hand off to the right
adapter and return ``ExtractedContent``. Unknown extensions raise
``UnsupportedExtensionError`` rather than silently passing through —
silent fallback would mask adapter coverage gaps.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from engine.adapters._template.contract import ExtractedContent
from engine.adapters.local_fs.image import extract_image
from engine.adapters.local_fs.pdf import extract_pdf
from engine.adapters.youtube.extractor import extract_youtube

if TYPE_CHECKING:
    from anthropic import Anthropic

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

    Today only YouTube is supported. Anything else raises
    ``UnsupportedUrlError`` rather than silently passing through —
    silent fallback would mask coverage gaps.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in _YOUTUBE_HOSTS:
        return await extract_youtube(url, client=client, summary_model=summary_model)

    raise UnsupportedUrlError(
        f"no adapter registered for host {host!r}; supported: {sorted(_YOUTUBE_HOSTS)}"
    )


__all__ = [
    "UnsupportedExtensionError",
    "UnsupportedUrlError",
    "extract",
    "extract_url",
]
