"""Gmail source adapter — ingest a thread by message/thread ID."""

from engine.adapters.gmail.extractor import (
    GmailExtractError,
    extract_gmail,
    extract_gmail_id,
)

__all__ = [
    "GmailExtractError",
    "extract_gmail",
    "extract_gmail_id",
]
