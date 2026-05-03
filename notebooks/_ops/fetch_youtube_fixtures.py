"""One-shot YouTube transcript cacher for Notebook 06.

Run from the repo root:

    uv run python notebooks/_ops/fetch_youtube_fixtures.py

Output: ``notebooks/data/transcripts/<video_id>.json`` for each video
in ``VIDEOS`` below, holding the raw segment list (text + start +
duration). Cells in NB 06 read these caches by default so reruns
don't depend on YouTube uptime.

To regenerate, just re-run; the script is idempotent. To add new
fixtures, append to ``VIDEOS`` and rerun.

Synthetic CJK fixture (``synthetic-cjk.json``) is also written here —
sourcing a real Japanese YouTube video is fragile (videos get pulled,
auto-captions change), so the CJK demo cell uses a deterministic
hand-built segment list.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "notebooks" / "data" / "transcripts"

# Stable, captioned, content-filter-safe videos. 3Blue1Brown's neural
# networks introduction has been live since 2017 with manual captions
# and educational content (math/CS) that summarizers handle cleanly.
# Music-video lyrics tend to trip content filters on summary, so we
# avoid those even though they're more universally known.
VIDEOS: list[tuple[str, str]] = [
    ("aircAruvnKk", "3Blue1Brown — But what is a neural network? (educational)"),
]

SYNTHETIC_CJK_SEGMENTS = [
    {"text": "皆さん、こんにちは。", "start": 0.0, "duration": 2.5},
    {"text": "今日はマージナリアエンジンについてお話しします。", "start": 2.5, "duration": 4.0},
    {"text": "このシステムは、ソースを取り込み、知識ベースに変換します。", "start": 6.5, "duration": 4.5},
    {"text": "アナライズステップとシンセサイズステップに分かれています。", "start": 11.0, "duration": 4.0},
    {"text": "アナライズはキャッシュ可能で決定論的です。", "start": 15.0, "duration": 3.5},
    {"text": "シンセサイズはWiki状態に依存します。", "start": 18.5, "duration": 3.0},
    {"text": "ご清聴ありがとうございました。", "start": 21.5, "duration": 2.0},
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    for video_id, label in VIDEOS:
        try:
            fetched = api.fetch(video_id)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {video_id} ({label}): {type(exc).__name__}: {exc}")
            continue
        raw = fetched.to_raw_data()
        target = OUT / f"{video_id}.json"
        target.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  wrote {target.relative_to(REPO)} — {len(raw)} segments — {label}")

    cjk_target = OUT / "synthetic-cjk.json"
    cjk_target.write_text(
        json.dumps(SYNTHETIC_CJK_SEGMENTS, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  wrote {cjk_target.relative_to(REPO)} — {len(SYNTHETIC_CJK_SEGMENTS)} synthetic CJK segments")


if __name__ == "__main__":
    main()
