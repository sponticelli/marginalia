# engine/agents/ — agent topology

The orchestrator-subagent pattern from design §7.1, implemented as
in-process Python functions invoked through the Anthropic Messages
API `tools=[...]` loop.

```
orchestrator (Sonnet 4.6)
├── spawn_ingest_agent
│       extract → analyze_source (Haiku 4.5) → synthesize_page (Sonnet 4.6)
│       (engine.adapters + engine.agents.ingest)
└── spawn_qa_agent
        qa_with_gap_detection (Sonnet 4.6)
        ├── thin retrieval → KnowledgeGap signal
        └── adequate retrieval → qa_single_question
        (separately: qa_with_decomposition for compound questions)
```

## Why in-process Python, not nested subprocesses

Two non-obvious facts settle this:

1. **Claude Agent SDK's `query()` requires the `claude` CLI subprocess** — it spawns the headless CLI and pipes JSON-RPC. In a development loop where the *parent* process is already a `claude` session (Claude Code, Cursor, an MCP-driven host), nested spawns fail authentication or hit recursion guards. We hit this in NB 06.

2. **The Messages API `tools=[...]` parameter already implements the agent loop semantics.** The orchestrator is one `messages.create` call in a loop; each `tool_use` block in the model's response gets executed as a Python function, and the result is appended as a `tool_result` block on the next turn. No new framework needed.

The trade-off: subagents share a process with the orchestrator, so they can't be killed independently or scaled to separate machines. For the PoC that's fine — production scaling lives downstream of correctness, and the function-call boundary is a clean enough abstraction that swapping in subprocesses or remote workers later is mechanical.

## Module layout

| Module | What lives there |
|---|---|
| `engine.agents.ingest` | Single-source ingest: analyze + synthesize, strict-schema retry. NB 02. |
| `engine.agents.synthesis` | Cross-source synthesis: read N source pages → produce one new Concept/Analysis/Decision page citing them all. NB 05. |
| `engine.agents.qa` | Three QA behaviors per §7.1: single-question, decomposition, gap detection. NB 07. |
| `engine.agents.orchestrator` | Routes user requests to the right subagent. NB 07. |
| `engine.agents.lint` | (TBD) Nightly contradiction/staleness pass. NB 08. |
| `engine.agents.scaffold` | (TBD) Index/purpose/AGENTS regeneration. Future. |

## Receipts

- `engine/decisions/orchestrator-and-qa.md` — token attribution, decomposition vs single A/B, gap-detection demo (NB 07).
- `engine/decisions/synthesis-patterns.md` — cross-source synthesis (NB 05).
- `engine/decisions/model-selection.md` — per-task model choices (NB 03).
