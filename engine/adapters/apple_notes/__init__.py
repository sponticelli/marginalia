"""Apple Notes source adapter — read notes via AppleScript (macOS only)."""

from engine.adapters.apple_notes.extractor import (
    AppleNotesExtractError,
    AppleNotesUnsupportedPlatform,
    extract_apple_note,
)

__all__ = [
    "AppleNotesExtractError",
    "AppleNotesUnsupportedPlatform",
    "extract_apple_note",
]
