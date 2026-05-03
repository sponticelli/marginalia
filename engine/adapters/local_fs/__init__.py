"""Local filesystem adapter — PDF + image extraction (design §8)."""

from engine.adapters.local_fs.image import (
    DEFAULT_MODEL as IMAGE_DEFAULT_MODEL,
)
from engine.adapters.local_fs.image import (
    PROMPT_NAME as IMAGE_PROMPT_NAME,
)
from engine.adapters.local_fs.image import (
    UnsupportedImageError,
    extract_image,
)
from engine.adapters.local_fs.pdf import (
    DEFAULT_MODEL as PDF_DEFAULT_MODEL,
)
from engine.adapters.local_fs.pdf import (
    DEFAULT_TEXT_THRESHOLD,
    extract_pdf,
    extract_pdf_via_rasterization,
)
from engine.adapters.local_fs.pdf import (
    PROMPT_NAME as PDF_PROMPT_NAME,
)

__all__ = [
    "DEFAULT_TEXT_THRESHOLD",
    "IMAGE_DEFAULT_MODEL",
    "IMAGE_PROMPT_NAME",
    "PDF_DEFAULT_MODEL",
    "PDF_PROMPT_NAME",
    "UnsupportedImageError",
    "extract_image",
    "extract_pdf",
    "extract_pdf_via_rasterization",
]
