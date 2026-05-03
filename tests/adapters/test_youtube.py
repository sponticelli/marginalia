"""YouTube adapter — URL parsing, transcript shape, graceful failure."""

from __future__ import annotations

import pytest

from engine.adapters._template.contract import ExtractedContent
from engine.adapters.youtube import extractor as yt
from engine.adapters.youtube.extractor import (
    CJK_WORD_BUDGET,
    LATIN_WORD_BUDGET,
    YoutubeUrlError,
    detect_cjk_ratio,
    extract_video_id,
    extract_youtube,
    format_segments,
    summary_word_budget,
)

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube.com/v/dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
    ],
)
def test_extract_video_id_all_forms(url: str) -> None:
    assert extract_video_id(url) == VID


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/video?v=dQw4w9WgXcQ",
        "https://vimeo.com/12345",
        "not-a-url-at-all",
        "https://www.youtube.com/",  # no video id
        "dQw4w9WgXcQ",  # bare id, not a url
    ],
)
def test_extract_video_id_rejects_non_youtube(url: str) -> None:
    with pytest.raises(YoutubeUrlError):
        extract_video_id(url)


@pytest.mark.asyncio
async def test_extract_youtube_uses_cached_segments_skips_summary(stub_client) -> None:
    """summary_model=None → no Anthropic call, transcript-only body."""
    cached = [
        {"text": "first line", "start": 0.0, "duration": 2.0},
        {"text": "second line", "start": 65.5, "duration": 3.0},
    ]
    result = await extract_youtube(
        f"https://youtu.be/{VID}",
        client=stub_client,
        summary_model=None,
        cached_segments=cached,
    )
    assert isinstance(result, ExtractedContent)
    assert result.extraction_method == "youtube_transcript"
    assert result.failure_reason is None
    assert result.cost_usd is None
    assert stub_client.calls == []
    assert "[00:00] first line" in result.text
    assert "[01:05] second line" in result.text
    assert result.pages == ["[00:00] first line", "[01:05] second line"]


@pytest.mark.asyncio
async def test_extract_youtube_runs_summary_when_model_set(stub_client) -> None:
    cached = [
        {"text": "Apollo launch sync notes from Q2.", "start": 0.0, "duration": 3.0},
    ]
    stub_client.texts = ["This is a summary of the video about Apollo's Q2 launch sync."]
    result = await extract_youtube(
        f"https://youtu.be/{VID}",
        client=stub_client,
        summary_model="claude-haiku-4-5",
        cached_segments=cached,
    )
    assert result.cost_usd is not None
    assert result.cost_usd > 0
    assert "Executive summary" in result.text
    assert "Full transcript" in result.text
    assert "Apollo's Q2 launch" in result.text
    assert len(stub_client.calls) == 1


@pytest.mark.asyncio
async def test_extract_youtube_invalid_url_returns_failure_reason(stub_client) -> None:
    result = await extract_youtube(
        "https://example.com/not-youtube", client=stub_client, summary_model=None
    )
    assert result.failure_reason is not None
    assert result.failure_reason.startswith("invalid_url:")
    assert result.text == ""
    assert stub_client.calls == []


@pytest.mark.asyncio
async def test_extract_youtube_no_transcript_returns_failure_reason(
    stub_client, monkeypatch
) -> None:
    """Mocked NoTranscriptFound from the API → failure_reason set, no exception."""

    def _fake_fetch(video_id: str, languages: tuple[str, ...]) -> str:
        return f"no_transcript_found: video {video_id}"

    monkeypatch.setattr(yt, "_fetch_transcript_raw", _fake_fetch)
    result = await extract_youtube(
        f"https://youtu.be/{VID}", client=stub_client, summary_model=None
    )
    assert result.failure_reason == f"no_transcript_found: video {VID}"
    assert result.text == ""


@pytest.mark.asyncio
async def test_extract_youtube_video_unavailable_returns_failure_reason(
    stub_client, monkeypatch
) -> None:
    monkeypatch.setattr(
        yt,
        "_fetch_transcript_raw",
        lambda video_id, languages: f"video_unavailable: {video_id}",  # noqa: ARG005
    )
    result = await extract_youtube(
        f"https://youtu.be/{VID}", client=stub_client, summary_model=None
    )
    assert result.failure_reason.startswith("video_unavailable:")


@pytest.mark.asyncio
async def test_extract_youtube_transcripts_disabled_returns_failure_reason(
    stub_client, monkeypatch
) -> None:
    monkeypatch.setattr(
        yt,
        "_fetch_transcript_raw",
        lambda video_id, languages: f"transcripts_disabled: {video_id}",  # noqa: ARG005
    )
    result = await extract_youtube(
        f"https://youtu.be/{VID}", client=stub_client, summary_model=None
    )
    assert result.failure_reason.startswith("transcripts_disabled:")


@pytest.mark.asyncio
async def test_extract_youtube_empty_transcript_returns_failure_reason(stub_client) -> None:
    """An empty cached_segments list reads as empty transcript, not 'no transcript'."""
    result = await extract_youtube(
        f"https://youtu.be/{VID}",
        client=stub_client,
        summary_model=None,
        cached_segments=[],
    )
    assert result.failure_reason == "empty_transcript"


def test_summary_word_budget_latin_returns_200() -> None:
    text = "This is a long passage written entirely in English with normal punctuation."
    assert summary_word_budget(text) == LATIN_WORD_BUDGET


def test_summary_word_budget_cjk_returns_400() -> None:
    text = "これは日本語のテストです。これは長い段落です。" * 5
    assert summary_word_budget(text) == CJK_WORD_BUDGET


def test_detect_cjk_ratio_ignores_punctuation() -> None:
    """Pure-CJK with punctuation should still register as nearly all CJK."""
    text = "こんにちは、世界！"
    ratio = detect_cjk_ratio(text)
    assert ratio > 0.99


def test_detect_cjk_ratio_empty_string() -> None:
    assert detect_cjk_ratio("") == 0.0


def test_format_segments_preserves_timestamps_under_an_hour() -> None:
    raw = [
        {"text": "first", "start": 0.0, "duration": 2.0},
        {"text": "second", "start": 65.5, "duration": 3.0},
        {"text": "third", "start": 599.9, "duration": 1.0},
    ]
    text, segments = format_segments(raw)
    assert segments[0] == "[00:00] first"
    assert segments[1] == "[01:05] second"
    assert segments[2] == "[09:59] third"
    assert text == "\n".join(segments)


def test_format_segments_uses_hh_mm_ss_past_an_hour() -> None:
    raw = [{"text": "long video", "start": 3725.0, "duration": 5.0}]
    _, segments = format_segments(raw)
    assert segments[0] == "[1:02:05] long video"


def test_format_segments_strips_newlines_in_text() -> None:
    raw = [{"text": "line one\nline two", "start": 0.0, "duration": 1.0}]
    _, segments = format_segments(raw)
    assert "\n" not in segments[0]
    assert segments[0] == "[00:00] line one line two"
