"""Source adapters (design §8). Top-level re-exports of the adapter contract."""

from engine.adapters._template.contract import ExtractedContent, ExtractionMethod
from engine.adapters.local_fs.image import UnsupportedImageError, extract_image
from engine.adapters.local_fs.pdf import extract_pdf, extract_pdf_via_rasterization

__all__ = [
    "ExtractedContent",
    "ExtractionMethod",
    "UnsupportedImageError",
    "extract_image",
    "extract_pdf",
    "extract_pdf_via_rasterization",
]
