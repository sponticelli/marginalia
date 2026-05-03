"""PDF adapter contract — text-first, document-block fallback, rasterization opt-in."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.adapters.local_fs import pdf as pdf_mod
from engine.adapters.local_fs.pdf import (
    DEFAULT_TEXT_THRESHOLD,
    extract_pdf,
    extract_pdf_via_rasterization,
)


def test_extract_pdf_text_path_skips_client(stub_client, monkeypatch, tmp_path: Path) -> None:
    """When the text layer is dense, no Anthropic call is made."""
    text_pages = ["Page one with plenty of words to clear the threshold." * 5]
    monkeypatch.setattr(pdf_mod, "_read_pages_text", lambda path: text_pages)

    fake_pdf = tmp_path / "dense.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")  # not real, but path-readable.

    result = extract_pdf(fake_pdf, client=stub_client)
    assert result.extraction_method == "text"
    assert result.cost_usd is None
    assert result.chars_per_page is not None
    assert result.chars_per_page > DEFAULT_TEXT_THRESHOLD
    assert stub_client.calls == []


def test_extract_pdf_falls_back_to_document_block(
    stub_client, minimal_config, monkeypatch, tmp_path: Path
) -> None:
    """Sparse text → one document-block call, returns vision_document."""
    monkeypatch.setattr(pdf_mod, "_read_pages_text", lambda path: ["", "", ""])
    stub_client.texts = ["extracted via document block"]

    fake_pdf = tmp_path / "scanned.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake bytes")

    result = extract_pdf(fake_pdf, client=stub_client)
    assert result.extraction_method == "vision_document"
    assert result.text == "extracted via document block"
    assert result.cost_usd is not None
    assert result.cost_usd > 0
    assert len(stub_client.calls) == 1

    call = stub_client.calls[0]
    content_blocks = call["messages"][0]["content"]
    assert content_blocks[0]["type"] == "document"
    assert content_blocks[0]["source"]["media_type"] == "application/pdf"
    assert content_blocks[1]["type"] == "text"


def test_extract_pdf_threshold_is_tunable(stub_client, monkeypatch, tmp_path: Path) -> None:
    """Caller-provided threshold overrides the default."""
    monkeypatch.setattr(pdf_mod, "_read_pages_text", lambda path: ["short"])
    stub_client.texts = ["forced vision"]

    fake_pdf = tmp_path / "borderline.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")

    # `len('short') == 5`, threshold=10 → vision branch.
    result = extract_pdf(fake_pdf, text_threshold=10, client=stub_client)
    assert result.extraction_method == "vision_document"

    # threshold=1 → text branch.
    stub_client.calls.clear()
    stub_client.texts = []
    result = extract_pdf(fake_pdf, text_threshold=1, client=stub_client)
    assert result.extraction_method == "text"
    assert stub_client.calls == []


def test_extract_pdf_via_rasterization_uses_image_blocks(
    stub_client, monkeypatch, tmp_path: Path
) -> None:
    """Rasterization path builds image blocks (not document blocks)."""
    fake_pdf = tmp_path / "any.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")

    class _FakePage:
        def render(self, scale: float = 2.0):  # noqa: ARG002
            class _FakeRender:
                def to_pil(self):
                    from PIL import Image

                    return Image.new("RGB", (4, 4), color="white")

            return _FakeRender()

    class _FakeDocument:
        def __init__(self, _path: str) -> None:
            self.pages = [_FakePage(), _FakePage()]

        def __len__(self) -> int:
            return len(self.pages)

        def __getitem__(self, idx: int) -> _FakePage:
            return self.pages[idx]

    fake_module = type("M", (), {"PdfDocument": _FakeDocument})
    monkeypatch.setitem(__import__("sys").modules, "pypdfium2", fake_module)

    stub_client.texts = ["rasterized output"]
    result = extract_pdf_via_rasterization(fake_pdf, client=stub_client)
    assert result.extraction_method == "vision_rasterized"
    assert result.text == "rasterized output"
    assert len(stub_client.calls) == 1
    content_blocks = stub_client.calls[0]["messages"][0]["content"]
    image_blocks = [b for b in content_blocks if b.get("type") == "image"]
    text_blocks = [b for b in content_blocks if b.get("type") == "text"]
    assert len(image_blocks) == 2  # one per fake page
    assert len(text_blocks) == 1
    assert all(b["source"]["media_type"] == "image/png" for b in image_blocks)


def test_extract_pdf_via_rasterization_max_pages_caps(
    stub_client, monkeypatch, tmp_path: Path
) -> None:
    fake_pdf = tmp_path / "long.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")

    class _FakePage:
        def render(self, scale: float = 2.0):  # noqa: ARG002
            class _R:
                def to_pil(self):
                    from PIL import Image

                    return Image.new("RGB", (4, 4))

            return _R()

    class _FakeDocument:
        def __init__(self, _path: str) -> None:
            self.pages = [_FakePage()] * 5

        def __len__(self) -> int:
            return len(self.pages)

        def __getitem__(self, idx: int) -> _FakePage:
            return self.pages[idx]

    monkeypatch.setitem(
        __import__("sys").modules, "pypdfium2", type("M", (), {"PdfDocument": _FakeDocument})
    )
    stub_client.texts = ["truncated output"]

    extract_pdf_via_rasterization(fake_pdf, client=stub_client, max_pages=2)
    image_blocks = [
        b for b in stub_client.calls[0]["messages"][0]["content"] if b.get("type") == "image"
    ]
    assert len(image_blocks) == 2  # capped, not 5


def test_extract_pdf_returns_extracted_content_shape(
    stub_client, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pdf_mod, "_read_pages_text", lambda path: ["dense " * 200])
    fake_pdf = tmp_path / "ok.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")

    result = extract_pdf(fake_pdf, client=stub_client)
    # All ExtractedContent fields are present and validate.
    assert isinstance(result.text, str)
    assert result.extraction_method in {"text", "vision_document"}
    assert result.pages is None or isinstance(result.pages, list)


@pytest.mark.parametrize(
    ("pages", "threshold", "expect_method"),
    [
        ([""], 100, "vision_document"),  # whitespace only
        (["a" * 50], 100, "vision_document"),  # below threshold
        (["a" * 200], 100, "text"),  # above threshold
        (["", "", "a" * 200], 100, "vision_document"),  # mean below threshold
    ],
)
def test_extract_pdf_threshold_branching(
    pages, threshold, expect_method, stub_client, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pdf_mod, "_read_pages_text", lambda path: pages)
    stub_client.texts = ["fallback"]
    fake_pdf = tmp_path / "p.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")
    result = extract_pdf(fake_pdf, text_threshold=threshold, client=stub_client)
    assert result.extraction_method == expect_method
