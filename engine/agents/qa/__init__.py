"""QA agent — three behaviors per design §7.1.

- Single-question: search → read → answer with citations.
- Decomposition: split compound questions, run sub-queries in parallel, merge.
- Gap detection: when retrieval is thin, return a structured ``KnowledgeGap``.
"""

from engine.agents.qa.decompose import (
    SubQuery,
    decompose_query,
    qa_with_decomposition,
)
from engine.agents.qa.gap_detection import (
    DEFAULT_MIN_PAGES,
    DEFAULT_MIN_SCORE,
    KnowledgeGap,
    qa_with_gap_detection,
)
from engine.agents.qa.single_question import QaAnswer, qa_single_question

__all__ = [
    "DEFAULT_MIN_PAGES",
    "DEFAULT_MIN_SCORE",
    "KnowledgeGap",
    "QaAnswer",
    "SubQuery",
    "decompose_query",
    "qa_single_question",
    "qa_with_decomposition",
    "qa_with_gap_detection",
]
