"""Image adapter — single image content block, structured description back."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.adapters.local_fs.image import (
    UnsupportedImageError,
    extract_image,
)


def test_extract_image_calls_vision_with_image_block(stub_client, tmp_path: Path) -> None:
    src = tmp_path / "diagram.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    stub_client.texts = ["Architecture diagram showing 3 components: A → B → C."]

    result = extract_image(src, client=stub_client)
    assert result.extraction_method == "vision_image"
    assert result.text.startswith("Architecture diagram")
    assert result.cost_usd is not None
    assert result.cost_usd > 0
    assert result.pages is None

    assert len(stub_client.calls) == 1
    content_blocks = stub_client.calls[0]["messages"][0]["content"]
    assert content_blocks[0]["type"] == "image"
    assert content_blocks[0]["source"]["media_type"] == "image/png"
    assert content_blocks[1]["type"] == "text"


@pytest.mark.parametrize(
    ("suffix", "expected_media"),
    [
        (".png", "image/png"),
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".gif", "image/gif"),
        (".webp", "image/webp"),
    ],
)
def test_extract_image_media_type_dispatch(
    suffix: str, expected_media: str, stub_client, tmp_path: Path
) -> None:
    src = tmp_path / f"img{suffix}"
    src.write_bytes(b"\x00" * 16)
    stub_client.texts = ["x"]

    extract_image(src, client=stub_client)
    block = stub_client.calls[0]["messages"][0]["content"][0]
    assert block["source"]["media_type"] == expected_media


def test_extract_image_rejects_unknown_extension(stub_client, tmp_path: Path) -> None:
    src = tmp_path / "weird.bmp"
    src.write_bytes(b"\x00")
    with pytest.raises(UnsupportedImageError, match="unsupported image extension"):
        extract_image(src, client=stub_client)


def test_extract_image_omits_temperature_for_opus(stub_client, tmp_path: Path) -> None:
    """Opus 4.7 deprecates `temperature`; the adapter must skip it for that model."""
    src = tmp_path / "x.png"
    src.write_bytes(b"\x00" * 16)
    stub_client.texts = ["x"]
    extract_image(src, client=stub_client, model="claude-opus-4-7")
    assert "temperature" not in stub_client.calls[0]
