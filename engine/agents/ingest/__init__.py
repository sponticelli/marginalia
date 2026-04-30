"""Ingest agent contracts and helpers (design §7.1, §7.3, §7.6 L1)."""

from engine.agents.ingest.analyze import (
    DEFAULT_MAX_TOKENS as ANALYZE_DEFAULT_MAX_TOKENS,
)
from engine.agents.ingest.analyze import (
    DEFAULT_MODEL as ANALYZE_DEFAULT_MODEL,
)
from engine.agents.ingest.analyze import (
    PROMPT_NAME as ANALYZE_PROMPT_NAME,
)
from engine.agents.ingest.analyze import (
    SourceAnalysis,
    analyze_source,
    build_analyze_system,
    compute_content_sha256,
)
from engine.agents.ingest.synthesize import (
    DEFAULT_MAX_TOKENS as SYNTH_DEFAULT_MAX_TOKENS,
)
from engine.agents.ingest.synthesize import (
    DEFAULT_MODEL as SYNTH_DEFAULT_MODEL,
)
from engine.agents.ingest.synthesize import (
    MAX_ATTEMPTS,
    AttemptRecord,
    build_synth_system,
    normalize_errors,
    parse_frontmatter_and_body,
    synthesize_page,
)
from engine.agents.ingest.synthesize import (
    PROMPT_NAME as SYNTH_PROMPT_NAME,
)

__all__ = [
    "ANALYZE_DEFAULT_MAX_TOKENS",
    "ANALYZE_DEFAULT_MODEL",
    "ANALYZE_PROMPT_NAME",
    "MAX_ATTEMPTS",
    "SYNTH_DEFAULT_MAX_TOKENS",
    "SYNTH_DEFAULT_MODEL",
    "SYNTH_PROMPT_NAME",
    "AttemptRecord",
    "SourceAnalysis",
    "analyze_source",
    "build_analyze_system",
    "build_synth_system",
    "compute_content_sha256",
    "normalize_errors",
    "parse_frontmatter_and_body",
    "synthesize_page",
]
