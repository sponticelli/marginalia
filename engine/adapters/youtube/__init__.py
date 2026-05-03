"""YouTube adapter (design §8 YouTube row)."""

from engine.adapters.youtube.extractor import (
    CJK_RATIO_THRESHOLD,
    CJK_WORD_BUDGET,
    DEFAULT_SUMMARY_MODEL,
    LATIN_WORD_BUDGET,
    PROMPT_NAME,
    YoutubeUrlError,
    detect_cjk_ratio,
    extract_video_id,
    extract_youtube,
    format_segments,
    summary_word_budget,
)

__all__ = [
    "CJK_RATIO_THRESHOLD",
    "CJK_WORD_BUDGET",
    "DEFAULT_SUMMARY_MODEL",
    "LATIN_WORD_BUDGET",
    "PROMPT_NAME",
    "YoutubeUrlError",
    "detect_cjk_ratio",
    "extract_video_id",
    "extract_youtube",
    "format_segments",
    "summary_word_budget",
]
