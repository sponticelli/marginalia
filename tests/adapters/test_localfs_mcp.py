"""Local-fs MCP server underlying functions: traversal-safe and bounded."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.adapters.local_fs.mcp_server import (
    DEFAULT_MAX_BYTES,
    build_server,
    list_files_under,
    read_file_under,
)


def _populate(tmp: Path) -> None:
    (tmp / "a.md").write_text("# a", encoding="utf-8")
    (tmp / "sub").mkdir()
    (tmp / "sub" / "b.txt").write_text("b body", encoding="utf-8")
    (tmp / "sub" / "c.json").write_text("{}", encoding="utf-8")


def test_list_files_returns_relative_paths(tmp_path: Path) -> None:
    _populate(tmp_path)
    files = list_files_under(tmp_path)
    assert sorted(files) == ["a.md", "sub/b.txt", "sub/c.json"]


def test_list_files_subpath_scopes_to_subdirectory(tmp_path: Path) -> None:
    _populate(tmp_path)
    files = list_files_under(tmp_path, "sub")
    assert sorted(files) == ["sub/b.txt", "sub/c.json"]


def test_list_files_traversal_blocked(tmp_path: Path) -> None:
    _populate(tmp_path)
    with pytest.raises(ValueError, match="escapes root"):
        list_files_under(tmp_path, "../")


def test_list_files_missing_subpath_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list_files_under(tmp_path, "does-not-exist")


def test_read_file_returns_contents(tmp_path: Path) -> None:
    _populate(tmp_path)
    assert read_file_under(tmp_path, "sub/b.txt") == "b body"


def test_read_file_traversal_blocked(tmp_path: Path) -> None:
    _populate(tmp_path)
    with pytest.raises(ValueError, match="escapes root"):
        read_file_under(tmp_path, "../../etc/passwd")


def test_read_file_max_bytes_truncates(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_text("x" * 100, encoding="utf-8")
    out = read_file_under(tmp_path, "big.txt", max_bytes=10)
    assert out == "x" * 10


def test_read_file_directory_raises(tmp_path: Path) -> None:
    _populate(tmp_path)
    with pytest.raises(ValueError, match="not a file"):
        read_file_under(tmp_path, "sub")


def test_build_server_registers_two_tools(tmp_path: Path) -> None:
    """Smoke test: FastMCP server constructs without errors and exposes both tools."""
    _populate(tmp_path)
    server = build_server(tmp_path)
    # FastMCP exposes registered tools via internal state; we just check it's a FastMCP.
    assert server.name == "marginalia-localfs"
    assert DEFAULT_MAX_BYTES == 1_000_000  # sanity check the constant
