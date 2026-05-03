"""YouTube source adapter (design §8 YouTube row, §10G).

Fetches transcripts via ``youtube-transcript-api`` (no API key required),
preserves ``[MM:SS]`` timestamps for citation, and optionally runs a
Haiku 4.5 executive summary (CJK-aware length budget).

Failure modes are *structured*: missing transcripts, unavailable
videos, parse errors all return ``ExtractedContent`` with
``failure_reason`` populated and ``text=""``. Callers can collect
partial success across a batch instead of catching exceptions per URL.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from engine.adapters._template.contract import ExtractedContent
from engine.prompts import Prompt, load_prompt
from engine.utils.api_compat import temperature_kwargs
from engine.utils.cost_tracker import estimate_cost_usd

if TYPE_CHECKING:
    from anthropic import Anthropic

PROMPT_NAME = "youtube_summary"
DEFAULT_SUMMARY_MODEL = "claude-haiku-4-5"
DEFAULT_SUMMARY_MAX_TOKENS = 1024
LATIN_WORD_BUDGET = 200
CJK_WORD_BUDGET = 400
CJK_RATIO_THRESHOLD = 0.30

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATH_VIDEO_ID_RE = re.compile(r"/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "music.youtube.com",
}


class YoutubeUrlError(ValueError):
    """Raised when a URL is not a recognizable YouTube URL."""


def extract_video_id(url: str) -> str:
    """Parse the 11-char video ID from any YouTube URL form.

    Handles: youtube.com/watch?v=, youtu.be/, embed/, shorts/, live/,
    m.youtube.com, music.youtube.com. Raises ``YoutubeUrlError`` on
    anything else (including bare 11-char strings — pass a URL).
    """
    parsed = urlparse(url)
    if parsed.hostname is None or parsed.hostname not in _YOUTUBE_HOSTS:
        raise YoutubeUrlError(f"not a YouTube URL: {url!r}")

    if parsed.hostname == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
        if _VIDEO_ID_RE.match(candidate):
            return candidate
        raise YoutubeUrlError(f"could not extract video id from {url!r}")

    qs = parse_qs(parsed.query or "")
    if "v" in qs and qs["v"]:
        candidate = qs["v"][0]
        if _VIDEO_ID_RE.match(candidate):
            return candidate

    match = _PATH_VIDEO_ID_RE.search(parsed.path or "")
    if match:
        return match.group(1)

    raise YoutubeUrlError(f"could not extract video id from {url!r}")


def detect_cjk_ratio(text: str) -> float:
    """Fraction of CJK Unicode codepoints over total *word-character* chars.

    "Word characters" excludes whitespace and punctuation so the ratio
    isn't inflated by long ASCII-only intervals between CJK words.
    """
    if not text:
        return 0.0
    cjk = 0
    total = 0
    for ch in text:
        if ch.isspace() or unicodedata.category(ch).startswith("P"):
            continue
        total += 1
        if _is_cjk(ch):
            cjk += 1
    return (cjk / total) if total else 0.0


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    # Common CJK ranges: Unified Ideographs, Hiragana, Katakana, Hangul.
    return (
        0x3040 <= code <= 0x30FF  # Hiragana + Katakana
        or 0x3400 <= code <= 0x4DBF  # CJK Extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
        or 0xAC00 <= code <= 0xD7AF  # Hangul Syllables
        or 0xF900 <= code <= 0xFAFF  # CJK Compatibility Ideographs
    )


def summary_word_budget(text: str) -> int:
    """Return 200 for Latin-dominant text, 400 if CJK ratio > 0.30."""
    return CJK_WORD_BUDGET if detect_cjk_ratio(text) > CJK_RATIO_THRESHOLD else LATIN_WORD_BUDGET


def _format_timestamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"[{h}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def format_segments(raw: list[dict]) -> tuple[str, list[str]]:
    """Build the joined transcript text + per-segment '[MM:SS] line' list.

    ``raw`` is the segment list shape that ``YouTubeTranscriptApi.fetch().to_raw_data()``
    returns: each entry has ``text``, ``start``, ``duration``.
    """
    segments: list[str] = []
    for seg in raw:
        ts = _format_timestamp(seg["start"])
        line = seg["text"].replace("\n", " ").strip()
        segments.append(f"{ts} {line}")
    return "\n".join(segments), segments


def _summarize(
    transcript: str,
    *,
    client: Anthropic,
    model: str,
    max_tokens: int,
    prompt: Prompt,
) -> tuple[str, float]:
    """Run the Haiku summary call. Returns (summary_text, cost_usd)."""
    word_budget = summary_word_budget(transcript)
    user_msg = prompt.user_template.format(transcript=transcript, word_budget=word_budget)
    system = prompt.system.replace("{word_budget}", str(word_budget))
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        **temperature_kwargs(model),
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = resp.content[0].text
    cost = estimate_cost_usd(model, resp.usage.input_tokens, resp.usage.output_tokens)
    return text, cost


def _failure(reason: str) -> ExtractedContent:
    return ExtractedContent(
        text="",
        pages=None,
        extraction_method="youtube_transcript",
        failure_reason=reason,
    )


def _fetch_transcript_raw(video_id: str, languages: tuple[str, ...]) -> list[dict] | str:
    """Sync transcript fetch. Returns raw segments on success, or a failure-reason string."""
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )

    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=languages)
    except VideoUnavailable as exc:
        return f"video_unavailable: {exc}"
    except TranscriptsDisabled as exc:
        return f"transcripts_disabled: {exc}"
    except NoTranscriptFound as exc:
        return f"no_transcript_found: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        return f"{type(exc).__name__}: {exc}"

    return fetched.to_raw_data()


async def extract_youtube(
    url: str,
    *,
    client: Anthropic | None = None,
    summary_model: str | None = DEFAULT_SUMMARY_MODEL,
    summary_max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS,
    languages: tuple[str, ...] = ("en",),
    prompt: Prompt | None = None,
    cached_segments: list[dict] | None = None,
) -> ExtractedContent:
    """Fetch a YouTube transcript and (optionally) summarize it.

    Returns ``ExtractedContent`` with:
    - ``extraction_method="youtube_transcript"``
    - ``text=summary + transcript`` (or transcript only if ``summary_model is None``)
    - ``pages``: list of ``"[MM:SS] line"`` strings (per-segment)
    - ``cost_usd``: USD spent on the optional Haiku summary call
    - ``failure_reason``: populated on graceful failure (transcript
      missing, video unavailable, etc.) — ``text`` is then ``""``.

    ``cached_segments`` lets callers (notebook fixtures, tests) bypass
    the live YouTube fetch entirely. When provided, the extractor uses
    those segments verbatim and skips the network call.
    """
    try:
        video_id = extract_video_id(url)
    except YoutubeUrlError as exc:
        return _failure(f"invalid_url: {exc}")

    if cached_segments is not None:
        raw = cached_segments
    else:
        result = await asyncio.to_thread(_fetch_transcript_raw, video_id, languages)
        if isinstance(result, str):
            return _failure(result)
        raw = result

    if not raw:
        return _failure("empty_transcript")

    transcript_text, segments = format_segments(raw)

    summary_text = ""
    cost_usd: float | None = None
    if summary_model is not None:
        if client is None:
            from anthropic import Anthropic as _Anthropic

            client = _Anthropic()
        prompt = prompt or load_prompt(PROMPT_NAME)
        summary_text, cost_usd = _summarize(
            transcript_text,
            client=client,
            model=summary_model,
            max_tokens=summary_max_tokens,
            prompt=prompt,
        )

    body = (
        f"## Executive summary\n\n{summary_text}\n\n## Full transcript\n\n{transcript_text}"
        if summary_text
        else transcript_text
    )

    return ExtractedContent(
        text=body,
        pages=segments,
        extraction_method="youtube_transcript",
        cost_usd=cost_usd,
        failure_reason=None,
    )


__all__ = [
    "CJK_RATIO_THRESHOLD",
    "CJK_WORD_BUDGET",
    "DEFAULT_SUMMARY_MAX_TOKENS",
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
