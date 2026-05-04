"""Generic web-page source adapter (design §8 web row)."""

from engine.adapters.web.extractor import (
    DEFAULT_USER_AGENT,
    WebFetchError,
    extract_web,
)

__all__ = [
    "DEFAULT_USER_AGENT",
    "WebFetchError",
    "extract_web",
]
