"""Multi-wiki registry and path resolver (design §9.6 / Phase 4).

Marginalia is single-wiki by default — every CLI verb assumes
``$WIKI_CONTENT_REPO``. This module adds an opt-in second layer:
a TOML registry at ``~/.config/marginalia/wikis.toml`` that maps
named wikis to their roots and lets ``marginalia use <name>`` flip
the active one.

Resolution order (highest priority first):

1. Explicit ``--wiki-root`` argument passed to a CLI verb.
2. ``$MARGINALIA_WIKI`` env var → looked up in the registry by name.
3. The ``active`` field in the registry (set by ``marginalia use``).
4. ``$WIKI_CONTENT_REPO`` env var (legacy / single-wiki fallback).
5. ``Path.cwd()`` (PoC / dev fallback).

A user with no registry behaves exactly as before — this module is
strictly additive. The registry comes into play only after the user
runs ``marginalia wikis add``.

The TOML schema is intentionally flat so we can hand-roll the writer
(no `tomli_w` dependency)::

    active = "personal"

    [wikis.personal]
    root = "/Users/sandro/wiki-personal"
    raw_path = "/Users/sandro/wiki-raw-personal"  # optional override

    [wikis.work]
    root = "/Users/sandro/wiki-work"
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RAW_PATH = Path.home() / "wiki-raw"


class WikiRegistryError(ValueError):
    """Raised on bad lookups (`use` of an unregistered name, etc.)."""


@dataclass(frozen=True)
class WikiEntry:
    """One row in the registry."""

    name: str
    root: Path
    raw_path: Path | None = None


@dataclass(frozen=True)
class WikiRegistry:
    """Parsed `wikis.toml` contents.

    A missing file maps to an empty registry — that's the "no opt-in"
    state. Mutations go through the helpers below; nothing here writes
    back to disk on its own.
    """

    wikis: dict[str, WikiEntry]
    active: str | None

    def entry(self, name: str) -> WikiEntry:
        if name not in self.wikis:
            raise WikiRegistryError(
                f"wiki {name!r} is not registered; run `marginalia wikis add {name} <root>` first"
            )
        return self.wikis[name]


# ─── config-file location (XDG) ──────────────────────────────────────


def config_path() -> Path:
    """Return the path to ``wikis.toml`` (XDG-respecting).

    Honors ``$XDG_CONFIG_HOME`` per the XDG Base Directory spec; falls
    back to ``~/.config/marginalia/wikis.toml`` otherwise. Tests inject
    ``$XDG_CONFIG_HOME`` to avoid touching the user's real config.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "marginalia" / "wikis.toml"


# ─── load / save ─────────────────────────────────────────────────────


def load_registry(path: Path | None = None) -> WikiRegistry:
    """Read the registry from disk; missing file → empty registry."""
    path = path or config_path()
    if not path.is_file():
        return WikiRegistry(wikis={}, active=None)

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    active = raw.get("active")
    wikis: dict[str, WikiEntry] = {}
    for name, body in (raw.get("wikis") or {}).items():
        if not isinstance(body, dict) or "root" not in body:
            # Skip malformed entries silently — protects against partial edits.
            continue
        wikis[name] = WikiEntry(
            name=name,
            root=Path(body["root"]).expanduser(),
            raw_path=(Path(body["raw_path"]).expanduser() if body.get("raw_path") else None),
        )
    if active is not None and active not in wikis:
        # `active` was deleted out from under us — reset rather than crash.
        active = None
    return WikiRegistry(wikis=wikis, active=active)


def save_registry(registry: WikiRegistry, path: Path | None = None) -> Path:
    """Write the registry; create parent dirs as needed. Returns the path."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_toml(registry), encoding="utf-8")
    return path


def _dump_toml(registry: WikiRegistry) -> str:
    """Hand-rolled TOML writer — schema is flat, no nested arrays.

    Avoiding the `tomli_w` dependency keeps the install footprint small
    and the format under our direct control. The escape rule is the
    minimal set TOML requires for basic strings: backslash and quote.
    """
    lines: list[str] = []
    if registry.active is not None:
        lines.append(f"active = {_quote(registry.active)}")
        lines.append("")
    for name in sorted(registry.wikis):
        entry = registry.wikis[name]
        lines.append(f"[wikis.{_bare_key(name)}]")
        lines.append(f"root = {_quote(str(entry.root))}")
        if entry.raw_path is not None:
            lines.append(f"raw_path = {_quote(str(entry.raw_path))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _bare_key(name: str) -> str:
    """TOML bare keys allow [A-Za-z0-9_-]; quote anything else."""
    if name and all(c.isalnum() or c in "_-" for c in name):
        return name
    return _quote(name)


# ─── mutation helpers (used by `marginalia wikis ...` verbs) ─────────


def add_wiki(
    name: str,
    root: Path,
    *,
    raw_path: Path | None = None,
    path: Path | None = None,
) -> WikiRegistry:
    """Register a wiki; sets ``active`` to it if it's the first one.

    Idempotent for repeat calls with the same name — overwrites the
    existing entry. Returns the new registry state (and writes it).
    """
    if not name:
        raise WikiRegistryError("wiki name must be non-empty")
    reg = load_registry(path)
    new_wikis = dict(reg.wikis)
    new_wikis[name] = WikiEntry(name=name, root=Path(root).expanduser(), raw_path=raw_path)
    new_active = reg.active if reg.active is not None else name
    new_reg = WikiRegistry(wikis=new_wikis, active=new_active)
    save_registry(new_reg, path)
    return new_reg


def remove_wiki(name: str, *, path: Path | None = None) -> WikiRegistry:
    """Drop a wiki; clear ``active`` if it was active."""
    reg = load_registry(path)
    if name not in reg.wikis:
        raise WikiRegistryError(f"wiki {name!r} is not registered")
    new_wikis = {k: v for k, v in reg.wikis.items() if k != name}
    new_active = reg.active if reg.active != name else None
    new_reg = WikiRegistry(wikis=new_wikis, active=new_active)
    save_registry(new_reg, path)
    return new_reg


def set_active(name: str, *, path: Path | None = None) -> WikiRegistry:
    """Flip the ``active`` field. Errors if name is not registered."""
    reg = load_registry(path)
    reg.entry(name)  # validates
    new_reg = WikiRegistry(wikis=reg.wikis, active=name)
    save_registry(new_reg, path)
    return new_reg


# ─── resolution (the public API every CLI verb calls) ────────────────


def resolve_wiki_root(explicit: Path | None = None, *, path: Path | None = None) -> Path:
    """Resolve the wiki root using the priority chain documented above.

    Always returns a ``Path`` — never raises. Callers that want to know
    *which* layer answered (for diagnostics) can call the layer-specific
    helpers directly.
    """
    if explicit is not None:
        return Path(explicit).expanduser()

    reg = load_registry(path)

    env_name = os.environ.get("MARGINALIA_WIKI")
    if env_name:
        try:
            return reg.entry(env_name).root
        except WikiRegistryError:
            # MARGINALIA_WIKI points at an unregistered name — fall through to
            # the next layer rather than hard-failing. The user gets predictable
            # behavior even if their shell is mis-configured.
            pass

    if reg.active is not None and reg.active in reg.wikis:
        return reg.wikis[reg.active].root

    legacy = os.environ.get("WIKI_CONTENT_REPO")
    if legacy:
        return Path(legacy).expanduser()

    return Path.cwd()


def resolve_raw_path(wiki_root: Path | None = None, *, path: Path | None = None) -> Path:
    """Resolve the staging inbox path.

    Order: per-wiki override in the registry → ``$WIKI_RAW_PATH`` →
    ``~/wiki-raw`` default. ``wiki_root`` is the already-resolved root
    used to find the per-wiki override.
    """
    wiki_root = wiki_root if wiki_root is not None else resolve_wiki_root(path=path)
    reg = load_registry(path)
    for entry in reg.wikis.values():
        if entry.root == wiki_root and entry.raw_path is not None:
            return entry.raw_path

    env = os.environ.get("WIKI_RAW_PATH")
    return Path(env).expanduser() if env else DEFAULT_RAW_PATH


__all__ = [
    "DEFAULT_RAW_PATH",
    "WikiEntry",
    "WikiRegistry",
    "WikiRegistryError",
    "add_wiki",
    "config_path",
    "load_registry",
    "remove_wiki",
    "resolve_raw_path",
    "resolve_wiki_root",
    "save_registry",
    "set_active",
]
