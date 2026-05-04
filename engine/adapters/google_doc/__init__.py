"""Google Docs source adapter (design §8 gdrive row, narrowed to Docs)."""

from engine.adapters.google_doc.extractor import (
    GoogleDocExtractError,
    extract_doc_id,
    extract_google_doc,
)

__all__ = [
    "GoogleDocExtractError",
    "extract_doc_id",
    "extract_google_doc",
]
