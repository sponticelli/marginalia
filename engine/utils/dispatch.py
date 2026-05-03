"""Extension → adapter routing (design §8 dispatch table).

`extract` is the entry point: given a path, hand off to the right
adapter and return ``ExtractedContent``. Unknown extensions raise
``UnsupportedExtensionError`` rather than silently passing through —
silent fallback would mask adapter coverage gaps.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from engine.adapters._template.contract import ExtractedContent
from engine.adapters.local_fs.image import extract_image
from engine.adapters.local_fs.pdf import extract_pdf

if TYPE_CHECKING:
    from anthropic import Anthropic

_TEXT_EXTENSIONS = {".md", ".txt"}
_PDF_EXTENSIONS = {".pdf"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class UnsupportedExtensionError(ValueError):
    """Raised when ``extract`` is asked about an unrouted extension."""


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


__all__ = ["UnsupportedExtensionError", "extract"]
