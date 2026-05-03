"""Extension dispatch — routes by suffix, raises on unknown."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.utils.dispatch import UnsupportedExtensionError, extract


def test_extract_text_passthrough_for_markdown(tmp_path: Path) -> None:
    src = tmp_path / "note.md"
    src.write_text("# hello\n", encoding="utf-8")
    result = extract(src)
    assert result.text == "# hello\n"
    assert result.extraction_method == "text"
    assert result.cost_usd is None
    assert result.pages is None


def test_extract_text_passthrough_for_txt(tmp_path: Path) -> None:
    src = tmp_path / "note.txt"
    src.write_text("plain text", encoding="utf-8")
    result = extract(src)
    assert result.text == "plain text"
    assert result.extraction_method == "text"


def test_extract_raises_on_unknown_extension(tmp_path: Path) -> None:
    src = tmp_path / "weird.xyz"
    src.write_text("nope", encoding="utf-8")
    with pytest.raises(UnsupportedExtensionError, match="no adapter registered"):
        extract(src)


def test_extract_routes_pdf_to_pdf_adapter(tmp_path: Path, monkeypatch) -> None:
    """``.pdf`` extensions go to ``extract_pdf`` — verified via monkeypatch."""
    captured: dict = {}

    def fake_extract_pdf(path, *, client=None):
        captured["path"] = Path(path)
        captured["client"] = client
        from engine.adapters._template.contract import ExtractedContent

        return ExtractedContent(text="stub-pdf", extraction_method="text")

    monkeypatch.setattr("engine.utils.dispatch.extract_pdf", fake_extract_pdf)
    fake_pdf = tmp_path / "doc.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")
    result = extract(fake_pdf)
    assert captured["path"] == fake_pdf
    assert result.text == "stub-pdf"


def test_extract_routes_png_to_image_adapter(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_extract_image(path, *, client=None):
        captured["path"] = Path(path)
        from engine.adapters._template.contract import ExtractedContent

        return ExtractedContent(text="stub-img", extraction_method="vision_image")

    monkeypatch.setattr("engine.utils.dispatch.extract_image", fake_extract_image)
    fake_png = tmp_path / "diagram.png"
    fake_png.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = extract(fake_png)
    assert captured["path"] == fake_png
    assert result.extraction_method == "vision_image"


@pytest.mark.parametrize("suffix", [".jpg", ".jpeg", ".gif", ".webp"])
def test_extract_routes_other_image_types(suffix: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "engine.utils.dispatch.extract_image",
        lambda path, *, client=None: __import__(
            "engine.adapters._template.contract", fromlist=["ExtractedContent"]
        ).ExtractedContent(text="img", extraction_method="vision_image"),
    )
    src = tmp_path / f"x{suffix}"
    src.write_bytes(b"\x00")
    result = extract(src)
    assert result.extraction_method == "vision_image"
