"""Shared test fixtures: stub Anthropic client and a minimal wiki config."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from engine.models.wiki_config import MarginaliaConfig


@dataclass
class _StubUsage:
    input_tokens: int = 100
    output_tokens: int = 20


@dataclass
class _StubContent:
    text: str


@dataclass
class _StubResponse:
    content: list[_StubContent]
    usage: _StubUsage


@dataclass
class StubAnthropicClient:
    """Minimal Anthropic stand-in that returns canned responses in order.

    Each call to `messages.create` pops the next `texts` entry. If the
    list runs out, the last entry is repeated — handy for "always fail"
    retry-loop tests.
    """

    texts: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.messages = _StubMessages(self)


@dataclass
class _StubMessages:
    parent: StubAnthropicClient

    def create(self, **kwargs):
        self.parent.calls.append(kwargs)
        if not self.parent.texts:
            text = ""
        elif len(self.parent.texts) == 1:
            text = self.parent.texts[0]
        else:
            text = self.parent.texts.pop(0)
        return _StubResponse(content=[_StubContent(text=text)], usage=_StubUsage())


@pytest.fixture
def stub_client() -> Iterator[StubAnthropicClient]:
    yield StubAnthropicClient()


@pytest.fixture
def minimal_config(tmp_path: Path) -> MarginaliaConfig:
    purpose = tmp_path / "purpose.md"
    agents = tmp_path / "AGENTS.md"
    purpose.write_text("# Purpose\n\nTest wiki.\n", encoding="utf-8")
    agents.write_text("# AGENTS\n\nUse customer, not client.\n", encoding="utf-8")
    return MarginaliaConfig.load(tmp_path)
