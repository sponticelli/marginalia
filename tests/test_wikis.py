"""Multi-wiki registry + path resolver (Phase 4 §4.1).

The resolver is load-bearing for every CLI verb after Phase 4 — the
fallback chain has to behave predictably or `marginalia use` becomes
worse than no multi-wiki at all. So every layer of the fallback gets
its own test.

The TOML round-trip + escape paths are tested separately because the
writer is hand-rolled (no `tomli_w` dep).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from engine.cli.wikis import (
    DEFAULT_RAW_PATH,
    WikiEntry,
    WikiRegistry,
    WikiRegistryError,
    add_wiki,
    config_path,
    load_registry,
    remove_wiki,
    resolve_raw_path,
    resolve_wiki_root,
    save_registry,
    set_active,
)


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `XDG_CONFIG_HOME` at a tmp dir so we never touch the user's real config.

    Also strip any inherited env vars that would leak through the fallback chain
    (MARGINALIA_WIKI, WIKI_CONTENT_REPO, WIKI_RAW_PATH) — each test opts in
    explicitly to the layers it wants to exercise.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("MARGINALIA_WIKI", raising=False)
    monkeypatch.delenv("WIKI_CONTENT_REPO", raising=False)
    monkeypatch.delenv("WIKI_RAW_PATH", raising=False)
    return config_path()


# ─── config_path() ───────────────────────────────────────────────────


def test_config_path_respects_xdg_config_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """`$XDG_CONFIG_HOME` overrides the `~/.config` default per spec."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/custom-xdg")
    assert config_path() == Path("/tmp/custom-xdg/marginalia/wikis.toml")


def test_config_path_falls_back_to_home_dot_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """No XDG var → `~/.config/marginalia/wikis.toml`."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_path() == Path.home() / ".config" / "marginalia" / "wikis.toml"


# ─── load / save round-trip ──────────────────────────────────────────


def test_missing_file_returns_empty_registry(isolated_config: Path) -> None:
    """No `wikis.toml` on disk → empty registry, no exception."""
    assert not isolated_config.exists()
    reg = load_registry()
    assert reg.wikis == {}
    assert reg.active is None


def test_save_then_load_round_trips(isolated_config: Path) -> None:
    """Write a non-trivial registry; read it back unchanged."""
    reg = WikiRegistry(
        wikis={
            "personal": WikiEntry(name="personal", root=Path("/tmp/wiki-personal")),
            "work": WikiEntry(
                name="work",
                root=Path("/tmp/wiki-work"),
                raw_path=Path("/tmp/raw-work"),
            ),
        },
        active="personal",
    )
    save_registry(reg)
    assert isolated_config.is_file()

    loaded = load_registry()
    assert loaded.active == "personal"
    assert set(loaded.wikis) == {"personal", "work"}
    assert loaded.wikis["personal"].root == Path("/tmp/wiki-personal")
    assert loaded.wikis["personal"].raw_path is None
    assert loaded.wikis["work"].raw_path == Path("/tmp/raw-work")


def test_dump_toml_is_valid_toml(isolated_config: Path) -> None:
    """Hand-rolled writer must produce something `tomllib.loads` accepts."""
    reg = WikiRegistry(
        wikis={"a-wiki": WikiEntry(name="a-wiki", root=Path("/x"))},
        active="a-wiki",
    )
    save_registry(reg)
    parsed = tomllib.loads(isolated_config.read_text())
    assert parsed["active"] == "a-wiki"
    assert parsed["wikis"]["a-wiki"]["root"] == "/x"


def test_dump_toml_escapes_special_characters(isolated_config: Path) -> None:
    """Quotes and backslashes in paths must round-trip."""
    weird_path = Path('/tmp/has "quotes" and \\backslash')
    reg = WikiRegistry(
        wikis={"w": WikiEntry(name="w", root=weird_path)},
        active="w",
    )
    save_registry(reg)
    loaded = load_registry()
    assert loaded.wikis["w"].root == weird_path


def test_load_skips_malformed_entries_silently(isolated_config: Path) -> None:
    """Partial edits (missing `root` field) shouldn't crash the resolver."""
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text(
        'active = "good"\n'
        "\n"
        "[wikis.good]\n"
        'root = "/tmp/good"\n'
        "\n"
        "[wikis.broken]\n"
        '# no root field\nraw_path = "/tmp/raw"\n',
        encoding="utf-8",
    )
    reg = load_registry()
    assert "good" in reg.wikis
    assert "broken" not in reg.wikis


def test_load_resets_dangling_active(isolated_config: Path) -> None:
    """If `active` points at a name that doesn't exist, treat as unset."""
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text(
        'active = "ghost"\n[wikis.real]\nroot = "/tmp/real"\n',
        encoding="utf-8",
    )
    reg = load_registry()
    assert reg.active is None
    assert "real" in reg.wikis


# ─── mutation helpers ────────────────────────────────────────────────


def test_add_wiki_first_one_becomes_active(isolated_config: Path) -> None:
    """First registered wiki auto-activates so `use` isn't required for one-wiki users."""
    reg = add_wiki("personal", Path("/tmp/personal"))
    assert reg.active == "personal"


def test_add_wiki_second_one_keeps_existing_active(isolated_config: Path) -> None:
    """Adding a second wiki doesn't silently steal `active`."""
    add_wiki("personal", Path("/tmp/personal"))
    reg = add_wiki("work", Path("/tmp/work"))
    assert reg.active == "personal"
    assert set(reg.wikis) == {"personal", "work"}


def test_add_wiki_overwrites_existing_entry(isolated_config: Path) -> None:
    """Re-adding under the same name updates the root rather than erroring."""
    add_wiki("p", Path("/tmp/old"))
    reg = add_wiki("p", Path("/tmp/new"))
    assert reg.wikis["p"].root == Path("/tmp/new")


def test_add_wiki_rejects_empty_name(isolated_config: Path) -> None:
    with pytest.raises(WikiRegistryError):
        add_wiki("", Path("/tmp/x"))


def test_remove_wiki_clears_active_if_active(isolated_config: Path) -> None:
    """Removing the active wiki must not leave `active` dangling."""
    add_wiki("p", Path("/tmp/p"))
    reg = remove_wiki("p")
    assert reg.active is None
    assert reg.wikis == {}


def test_remove_wiki_leaves_active_alone_if_other(isolated_config: Path) -> None:
    add_wiki("a", Path("/tmp/a"))
    add_wiki("b", Path("/tmp/b"))
    set_active("b")
    reg = remove_wiki("a")
    assert reg.active == "b"


def test_remove_wiki_unknown_errors(isolated_config: Path) -> None:
    with pytest.raises(WikiRegistryError):
        remove_wiki("ghost")


def test_set_active_validates_membership(isolated_config: Path) -> None:
    add_wiki("a", Path("/tmp/a"))
    with pytest.raises(WikiRegistryError):
        set_active("ghost")


# ─── resolve_wiki_root() — the full priority chain ───────────────────


def test_resolve_explicit_wins(isolated_config: Path) -> None:
    """An explicit arg beats every other layer."""
    add_wiki("p", Path("/tmp/p"))
    assert resolve_wiki_root(Path("/tmp/explicit")) == Path("/tmp/explicit")


def test_resolve_marginalia_wiki_env_beats_active(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`$MARGINALIA_WIKI` overrides whatever `use` set."""
    add_wiki("p", Path("/tmp/p"))
    add_wiki("w", Path("/tmp/w"))  # `p` stays active
    monkeypatch.setenv("MARGINALIA_WIKI", "w")
    assert resolve_wiki_root() == Path("/tmp/w")


def test_resolve_active_when_no_env(isolated_config: Path) -> None:
    """No env override → fall back to the registry's `active`."""
    add_wiki("p", Path("/tmp/p"))
    assert resolve_wiki_root() == Path("/tmp/p")


def test_resolve_legacy_wiki_content_repo(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No registry, no env override → legacy `$WIKI_CONTENT_REPO`."""
    monkeypatch.setenv("WIKI_CONTENT_REPO", "/tmp/legacy")
    assert resolve_wiki_root() == Path("/tmp/legacy")


def test_resolve_cwd_is_final_fallback(
    isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing configured → `Path.cwd()`."""
    monkeypatch.chdir(tmp_path)
    assert resolve_wiki_root() == tmp_path


def test_resolve_marginalia_wiki_unknown_falls_through(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`$MARGINALIA_WIKI=ghost` doesn't error — it falls through to the next layer."""
    add_wiki("p", Path("/tmp/p"))  # `p` becomes active
    monkeypatch.setenv("MARGINALIA_WIKI", "ghost")
    # Ghost falls through; `active` answers.
    assert resolve_wiki_root() == Path("/tmp/p")


# ─── resolve_raw_path() ──────────────────────────────────────────────


def test_resolve_raw_path_per_wiki_override_wins(isolated_config: Path) -> None:
    """A `raw_path` set on the entry beats `$WIKI_RAW_PATH`."""
    add_wiki("p", Path("/tmp/p"), raw_path=Path("/tmp/raw-p"))
    assert resolve_raw_path(Path("/tmp/p")) == Path("/tmp/raw-p")


def test_resolve_raw_path_env_var_when_no_override(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_wiki("p", Path("/tmp/p"))
    monkeypatch.setenv("WIKI_RAW_PATH", "/tmp/env-raw")
    assert resolve_raw_path(Path("/tmp/p")) == Path("/tmp/env-raw")


def test_resolve_raw_path_default_when_nothing_set(isolated_config: Path) -> None:
    """No registry override, no env var → ~/wiki-raw."""
    add_wiki("p", Path("/tmp/p"))
    assert resolve_raw_path(Path("/tmp/p")) == DEFAULT_RAW_PATH


def test_resolve_raw_path_resolves_wiki_root_if_omitted(isolated_config: Path) -> None:
    """`resolve_raw_path()` with no arg should self-resolve the wiki root."""
    add_wiki("p", Path("/tmp/p"), raw_path=Path("/tmp/raw-p"))
    assert resolve_raw_path() == Path("/tmp/raw-p")


# ─── independence from real env (regression guard) ───────────────────


def test_isolated_config_does_not_touch_real_home(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture must isolate writes — no real `~/.config/marginalia` mutations."""
    add_wiki("p", Path("/tmp/p"))
    # The isolated config dir is under tmp_path, not $HOME.
    assert isolated_config.is_file()
    real_home_path = Path.home() / ".config" / "marginalia" / "wikis.toml"
    # We're not asserting `not exists` (the user might genuinely have one)
    # — we're asserting that our writes went through the XDG override.
    assert isolated_config != real_home_path or os.environ.get("XDG_CONFIG_HOME") == str(
        Path.home() / ".config"
    )
