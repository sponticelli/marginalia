"""Local-filesystem PDF adapter (design §8 dispatch).

Two extraction paths, both returning ``ExtractedContent``:

- ``extract_pdf`` — the default. Tries ``pypdf`` text-layer extraction
  first; if the page is text-sparse (mean chars/page below threshold,
  or every page whitespace), falls back to a single Anthropic
  document content block. One API call max.

- ``extract_pdf_via_rasterization`` — opt-in alternative. Renders each
  page to PNG via ``pypdfium2`` and sends one image content block per
  page in a single Messages call. Useful when the caller needs
  page-by-page control or wants to bypass document-block size limits.
  Costs more tokens than the document path; exposed for transparency.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import TYPE_CHECKING

import pypdf

from engine.adapters._template.contract import ExtractedContent
from engine.prompts import Prompt, load_prompt
from engine.utils.api_compat import temperature_kwargs
from engine.utils.cost_tracker import estimate_cost_usd

if TYPE_CHECKING:
    from anthropic import Anthropic

PROMPT_NAME = "pdf_extract"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEXT_THRESHOLD = 100
DEFAULT_RASTER_SCALE = 2.0


def _read_pages_text(path: Path) -> list[str]:
    reader = pypdf.PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


def _is_text_sparse(pages: list[str], threshold: int) -> bool:
    if not pages:
        return True
    if all(not p.strip() for p in pages):
        return True
    chars = sum(len(p) for p in pages)
    return (chars / len(pages)) < threshold


def _mean_chars_per_page(pages: list[str]) -> float:
    if not pages:
        return 0.0
    return sum(len(p) for p in pages) / len(pages)


def _document_block_user_message(prompt: Prompt) -> str:
    return prompt.user_template.format(kind="PDF document")


def _build_doc_block(pdf_bytes: bytes) -> dict:
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
        },
    }


def _build_image_block(png_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(png_bytes).decode("ascii"),
        },
    }


def extract_pdf(
    path: Path | str,
    *,
    client: Anthropic | None = None,
    text_threshold: int = DEFAULT_TEXT_THRESHOLD,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    prompt: Prompt | None = None,
) -> ExtractedContent:
    """Extract text from a PDF: text layer first, document-block fallback.

    Routes by content density. Pure ``pypdf`` extraction is free and
    deterministic; the vision fallback only runs when the text layer
    looks scanned/empty. The threshold is tunable for callers with
    weird PDFs (e.g. dense headers but no body text).
    """
    pdf_path = Path(path)
    pages = _read_pages_text(pdf_path)
    cpp = _mean_chars_per_page(pages)

    if not _is_text_sparse(pages, text_threshold):
        joined = "\n\n".join(p.strip() for p in pages if p.strip())
        return ExtractedContent(
            text=joined,
            pages=pages,
            extraction_method="text",
            chars_per_page=cpp,
            cost_usd=None,
        )

    # Text-sparse → call Anthropic with the PDF as a native document block.
    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    prompt = prompt or load_prompt(PROMPT_NAME)
    pdf_bytes = pdf_path.read_bytes()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        **temperature_kwargs(model),
        system=prompt.system,
        messages=[
            {
                "role": "user",
                "content": [
                    _build_doc_block(pdf_bytes),
                    {"type": "text", "text": _document_block_user_message(prompt)},
                ],
            }
        ],
    )
    text = resp.content[0].text
    cost = estimate_cost_usd(model, resp.usage.input_tokens, resp.usage.output_tokens)
    return ExtractedContent(
        text=text,
        pages=pages or None,
        extraction_method="vision_document",
        chars_per_page=cpp,
        cost_usd=cost,
    )


def extract_pdf_via_rasterization(
    path: Path | str,
    *,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_pages: int | None = None,
    scale: float = DEFAULT_RASTER_SCALE,
    prompt: Prompt | None = None,
) -> ExtractedContent:
    """Render PDF pages to PNG and extract via image content blocks.

    Single ``messages.create`` call carrying one image block per page.
    Cost scales with page count (~each rasterized page is 1500-2500
    input tokens at scale=2). Use ``max_pages`` to cap for cost
    control on long documents; pages beyond the cap are skipped.
    """
    import pypdfium2

    pdf_path = Path(path)
    document = pypdfium2.PdfDocument(str(pdf_path))
    page_count = len(document)
    n_pages = min(page_count, max_pages) if max_pages else page_count

    image_blocks: list[dict] = []
    for index in range(n_pages):
        page = document[index]
        pil_image = page.render(scale=scale).to_pil()
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        image_blocks.append(_build_image_block(buf.getvalue()))

    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    prompt = prompt or load_prompt(PROMPT_NAME)
    user_text = prompt.user_template.format(kind=f"{n_pages}-page rasterized PDF")

    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        **temperature_kwargs(model),
        system=prompt.system,
        messages=[
            {
                "role": "user",
                "content": [*image_blocks, {"type": "text", "text": user_text}],
            }
        ],
    )
    text = resp.content[0].text
    cost = estimate_cost_usd(model, resp.usage.input_tokens, resp.usage.output_tokens)
    return ExtractedContent(
        text=text,
        pages=None,  # rasterization returns one consolidated text, not per-page split.
        extraction_method="vision_rasterized",
        chars_per_page=None,
        cost_usd=cost,
    )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_RASTER_SCALE",
    "DEFAULT_TEXT_THRESHOLD",
    "PROMPT_NAME",
    "extract_pdf",
    "extract_pdf_via_rasterization",
]
