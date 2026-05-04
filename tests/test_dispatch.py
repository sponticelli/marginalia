"""Extension dispatch — routes by suffix, raises on unknown."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.utils.dispatch import (
    UnsupportedExtensionError,
    UnsupportedUrlError,
    extract,
    route_path,
    route_url,
)


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


# ─── Phase 4: route_path / route_url (resync routing keys) ───────────


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("note.md", "local_fs_text"),
        ("note.txt", "local_fs_text"),
        ("doc.pdf", "local_fs_pdf"),
        ("img.png", "local_fs_image"),
        ("img.jpeg", "local_fs_image"),
    ],
)
def test_route_path_returns_adapter_name(filename: str, expected: str, tmp_path: Path) -> None:
    """`route_path` returns the adapter name without doing any I/O."""
    assert route_path(tmp_path / filename) == expected


def test_route_path_unknown_extension_raises() -> None:
    with pytest.raises(UnsupportedExtensionError):
        route_path(Path("/tmp/weird.xyz"))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://docs.google.com/document/d/foo/edit", "google_doc"),
        ("https://mail.google.com/mail/u/0/#inbox/abc123", "gmail"),
        ("https://www.notion.so/page-abc-123", "notion"),
        ("https://acme.slack.com/archives/C001/p1700000000000000", "slack"),
        ("https://github.com/owner/repo/issues/42", "github"),
        ("https://github.com/owner/repo/pull/13", "github"),
        ("https://github.com/owner/repo/discussions/9", "github"),
        ("notes://note-id-12345", "apple_notes"),
        # Generic web fallback for everything else.
        ("https://example.com/article", "web"),
        # github.com browsing URLs (no /issues/, /pull/, /discussions/) → web.
        ("https://github.com/owner/repo/blob/main/README.md", "web"),
    ],
)
def test_route_url_returns_adapter_name(url: str, expected: str) -> None:
    """`route_url` mirrors the routing decision in `extract_url`."""
    assert route_url(url) == expected


def test_route_url_unknown_scheme_raises() -> None:
    with pytest.raises(UnsupportedUrlError):
        route_url("ftp://example.com/whatever")
