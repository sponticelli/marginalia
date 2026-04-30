# Company Wiki PoC — Notebook Plan

**Document version: v0.2**
**Aligned with:** design.md v0.4
**Last updated:** 2026-04-30

A sequenced set of 11 Jupyter notebooks that prove the architecture in `marginalia-design.md` works end-to-end, while doubling as Anthropic Architect exam preparation.

## Changelog

- **v0.2 (2026-04-30)** — Aligned with design v0.4. Folded YouTube adapter into NB 06. Expanded NB 07 with query decomposition + knowledge gap detection. Added three new notebooks: NB 09 (job queue), NB 10 (3-layer cache + CACHE_VERSION), NB 11 (audit DB + hooks + dashboard.md). Added `purpose.md`/`AGENTS.md` work to NB 01. Total time: 38–48h.
- **v0.1 (2026-04-29)** — Original 8-notebook plan against design v0.2.

---

**Conventions for every notebook:**
- Markdown cell at top stating: purpose, exam domain(s) covered, dependencies on prior notebooks, design.md sections referenced.
- All Pydantic models, prompts, and agent code stabilized in a notebook get extracted to the `engine/` repo at the end.
- `nbstripout` runs as pre-commit; outputs are demonstration-only, never source-of-truth.
- Each notebook ends with a "What to extract" cell listing the files written to `engine/`.
- Costs and latencies are logged per call. Receipts go into `engine/decisions/model-selection.md`.

**Total estimated time:** 38–48 focused hours. Comfortably 6–8 weeks of evenings.

## At a glance

| # | Notebook | Time | Cert domain |
|---|---|---|---|
| 01 | Page schemas, frontmatter, `purpose.md` / `AGENTS.md` | 2–3h | Foundation |
| 02 | First API call + strict-schema retry + cacheable analyze step | 2–3h | API/SDK + PE |
| 03 | Haiku vs Sonnet vs Opus comparison | 1–2h | Models & Capabilities |
| 04 | Multi-modal: PDFs + images + vision fallback | 3–4h | Models & Capabilities |
| 05 | Synthesis across multiple sources | 2–3h | PE + Architecture |
| 06 | Custom MCP server with FastMCP + YouTube adapter | 5–6h | **Tool Use** (highest exam value) |
| 07 | Orchestrator + ingest + QA with decomposition + gap detection | 5–6h | Architecture Patterns |
| 08 | Lint pass + archive with sync backlink rewrite | 5–6h | Architecture + deep PE |
| 09 | SQLite job queue + worker + retry | 4–5h | Architecture Patterns |
| 10 | 3-layer cache + `CACHE_VERSION` invalidation | 2–3h | API/SDK + cost engineering |
| 11 | Audit DB + hooks + Dataview `dashboard.md` | 3–4h | Architecture + observability |

---

## Notebook 01 — Page schemas, frontmatter, and wiki-level config

**Name:** 01_page_schemas
**Purpose:** Establish the data layer. Zero API calls. Everything downstream depends on these models.
**Exam relevance:** Indirect (foundation).
**Design refs:** §6 page schema, §6.4 `purpose.md`/`AGENTS.md`, §7.3.1 strict schema enforcement.
**Depends on:** Nothing.
**Estimated time:** 2–3 hours.

**Cells:**
- Define Pydantic v2 models: `PageType` enum; `BasePage`, then subclasses `EntityPage`, `SourcePage`, `DecisionPage`, `ConceptPage`, `MeetingPage`, `MetricPage`, `QAPage`, `AnalysisPage`.
- Required vs conditional fields per type (mirrors §6.2 contracts).
- Round-trip test with `python-frontmatter`: parse a hand-written `.md` file → instantiate model → modify → serialize → re-parse → assert equal.
- Export JSON schemas via `model.model_json_schema()` to a local `_ops/schemas/` folder.
- Negative tests: feed deliberately malformed frontmatter; confirm validation errors are descriptive.
- Quick visualization: render a sample page in the notebook with formatted frontmatter highlighting.
- **Wiki-level config files.** Hand-write a sample `purpose.md` and `AGENTS.md`. Define `marginaliaConfig` as a Pydantic model that loads both. Demonstrate how a future ingest agent would load `marginaliaConfig` once and pass it as system-prompt material — they don't have a body schema like pages do, but they have structure (scope rules, terminology rules) that the agent should treat as authoritative.

**What to extract:**
- `engine/models/pages.py` — all Pydantic page models
- `engine/models/wiki_config.py` — `marginaliaConfig` loader for `purpose.md` + `AGENTS.md`
- `engine/models/__init__.py` — clean exports
- `_ops/schemas/*.json` — the JSON schemas for the wiki repo

---

## Notebook 02 — First API call + strict-schema retry + the cacheable analyze step

**Name:** 02_ingest_retry
**Purpose:** First real ingest. Implement the §7.3.1 retry pattern end-to-end on a single source. Make the *analyze* step explicitly cacheable so NB 10 has something to cache.
**Exam relevance:** API & SDK Usage (20%), Prompt Engineering (20%).
**Design refs:** §7.1 ingest agent (two-step), §7.3 prompting patterns, §7.3.1 strict schema, §10 Scenario A.
**Depends on:** NB 01 (uses `EntityPage`, `SourcePage`, `marginaliaConfig`).
**Estimated time:** 2–3 hours.

**Cells:**
- Claude Agent SDK setup: API key, first `query()` call to confirm connectivity.
- **Step 1: Analyze.** Function `analyze_source(content) -> SourceAnalysis` that extracts entities, classifies type, summarizes — but does not commit anything. Pure function: same input → same output. Returns a Pydantic `SourceAnalysis` model.
- **Step 2: Synthesize.** Function `synthesize_page(analysis, hint, existing_pages) -> SourcePage` that produces the strict-schema page.
- The reason for the split: step 1 is the *cacheable* one. Same source content + same prompt version → same analysis. Step 2 depends on the live state of the wiki and isn't worth caching the same way.
- Ingest prompt v1: XML-tagged `<source>` block + `<schema>` constraint + few-shot examples for entity extraction. `marginaliaConfig.purpose` content is appended to the system prompt so the agent has scope rules.
- Single Haiku 4.5 call on a markdown source; parse output; validate against `SourceAnalysis` then `SourcePage`.
- Retry loop: on `ValidationError`, feed the error back as `<validation_errors>` and re-run (max 3 attempts).
- Edge case: deliberately bad input that fails 3 times → produce `status: draft` page with `validation_errors` populated. Confirm it lands as a draft, not a silent failure.
- Token + cost logging per attempt; print summary table.
- A/B between two prompt versions to see which retries less.

**What to extract:**
- `engine/agents/ingest/analyze.py` — `analyze_source()` (the cacheable step)
- `engine/agents/ingest/synthesize.py` — `synthesize_page()`
- `engine/agents/ingest/__init__.py` — exports
- `engine/prompts/ingest_analyze.md` — versioned prompt for step 1
- `engine/prompts/ingest_synthesize.md` — versioned prompt for step 2
- `engine/utils/cost_tracker.py` — token/cost logger

---

## Notebook 03 — Model comparison: Haiku vs Sonnet vs Opus

**Name:** 03_model_comparison
**Purpose:** Generate receipts for the §7.2 model selection table. Pure exam-domain practice.
**Exam relevance:** Models & Capabilities (17%) — the exam loves specific tradeoffs.
**Design refs:** §7.2 model selection rationale.
**Depends on:** NB 02 (uses the ingest function).
**Estimated time:** 1–2 hours.

**Cells:**
- Same source document; run ingest 3x (once per model) with identical prompt.
- Side-by-side display: rendered output pages.
- Diff cells: which entities did each model extract? Where did they disagree?
- Quality probes: ask each model to summarize the same body in 50 words; compare faithfulness to source.
- Cost/latency/quality 3-axis chart (matplotlib).
- Run synthesis (multi-page reasoning) on a 5-page input; observe where Haiku starts to break down vs Sonnet vs Opus.
- Final cell: write a short markdown table with timestamped findings — this becomes the receipts file.

**What to extract:**
- `engine/decisions/model-selection.md` — the rationale table with timestamped receipts (lives in engine repo, not wiki)
- `engine/utils/model_comparison.py` — the harness for re-running the comparison when prices/models change

---

## Notebook 04 — Multi-modal ingest: PDFs and images

**Name:** 04_multimodal_ingest
**Purpose:** Demonstrate the multi-modal dispatch path. PDF text-first with vision fallback; image-only via vision.
**Exam relevance:** Models & Capabilities (multi-modal handling), Architecture Patterns (dispatch).
**Design refs:** §8 source adapters (file-extension dispatch), §10 Scenario G.
**Depends on:** NB 02 (ingest with retry).
**Estimated time:** 3–4 hours.

**Cells:**
- Sample inputs: one clean PDF, one scanned PDF, one architecture diagram PNG.
- PDF text path: `pypdf` extraction → ingest agent → `SourcePage`. Display extracted text.
- PDF vision fallback: rasterize each page with `pdf2image` or `pypdfium2` → Claude Vision per page → consolidate.
- Auto-dispatch logic: try text first; if extracted text is below a threshold (chars per page) or fails heuristic checks, switch to vision.
- Image-only ingest: PNG → Claude Vision → structured description → ingest agent → `SourcePage`. Display the model's interpretation alongside the image.
- Comparison: same content as PDF text and as PDF→vision; observe what gets lost or gained.
- Cost note: vision calls are expensive — log token deltas vs text-only.

**What to extract:**
- `engine/adapters/local_fs/pdf.py` — text-then-vision dispatch
- `engine/adapters/local_fs/image.py` — vision-based ingest
- `engine/utils/dispatch.py` — extension → adapter routing

---

## Notebook 05 — Synthesis across multiple sources

**Name:** 05_cross_source_synthesis
**Purpose:** Demonstrate cross-page reasoning. Synthesis agent reads N source pages and produces a concept page citing all of them.
**Exam relevance:** Prompt Engineering (XML, CoT), Architecture Patterns (RAG-vs-compounding-wiki).
**Design refs:** §7.1 synthesis agent, §10 Scenario A step 5.
**Depends on:** NB 02, 04 (need real source pages to synthesize from).
**Estimated time:** 2–3 hours.

**Cells:**
- Stage 3 pre-ingested source pages in a local `poc/sources/` folder (the markdown clip, the PDF whitepaper, the architecture image from notebook 04).
- Synthesis prompt: `<existing_pages>` + `<new_sources>` + `<thinking>` (CoT required) + `<output>` schema-constrained.
- Sonnet 4.6 call producing a `ConceptPage` that cites all three sources by `[[path]]` reference.
- Validation: confirm every citation in the output corresponds to a real source path. Fail loud if not.
- Hint-driven variant: pass `synthesis_hint="platform engineering theme"` — observe how output shifts.
- Edge case: feed two contradictory sources; observe whether Sonnet flags the conflict (it usually does) and how that surfaces in the output.

**What to extract:**
- `engine/agents/synthesis/cross_source.py` — synthesis function
- `engine/prompts/synthesis.md`

---

## Notebook 06 — Custom MCP server + the YouTube adapter

**Name:** 06_mcp_youtube_adapter
**Purpose:** Build a tiny MCP server end-to-end *and* a real source adapter (YouTube). This is the most cert-valuable notebook — Tool Use is canonical exam material — and the YouTube exercise gives the abstract MCP work a concrete payload.
**Exam relevance:** Tool Use (cross-cutting), Architecture Patterns (17%).
**Design refs:** §5.1 source layer, §7.4 tool use, §8 source adapters (YouTube row), §9.6 URL pattern dispatch.
**Depends on:** NB 02 (need an agent to call the tools).
**Estimated time:** 5–6 hours.

### Part A — FastMCP basics (~2h)

- Define a server with `list_files(path)` and `read_file(path)` tools targeting `~/wiki-raw/`.
- Run the server in a background subprocess from the notebook (or via stdio transport).
- Connect a Claude Agent SDK agent to the MCP server via `ClaudeAgentOptions(mcp_servers={...})`.
- Agent flow: user asks "ingest the new files in raw/" → agent calls `list_files` → calls `read_file` for each → calls the ingest function from NB 02 → returns a list of staged pages.
- Trace tool calls: log every MCP request/response for inspection.
- Compare: same workflow with Anthropic's official `filesystem` MCP vs your custom one. Show that the custom one wins when you need source-specific logic (file-type dispatch, ingest budgets).

### Part B — YouTube adapter as a concrete example (~3h)

- URL parsing: `_extract_video_id()` for all forms (`youtube.com/watch?v=`, `youtu.be/`, `embed/`, `shorts/`, `live/`). Parametrized tests.
- Transcript fetching with `youtube-transcript-api`, wrapped in `asyncio.to_thread` since the library is sync.
- Timestamp formatting: `[MM:SS]` prefixes for inline citation.
- Graceful degradation: handle `NoTranscriptFound`, `VideoUnavailable`, parse-failure — each returns a structured "failed-with-reason" result, not a crash.
- Optional LLM enrichment: executive summary via Haiku 4.5 with CJK-aware length (200 Latin / 400 CJK words). Without an LLM, return transcript-only.
- Final output: a `YoutubeExtraction` dataclass that maps onto a `SourcePage` body.
- **Wire it into the dispatcher.** `marginalia add https://youtu.be/...` should now route through this adapter end-to-end.

**Why combine these into one notebook.** The MCP-server work is abstract; the YouTube adapter is concrete. Having both in the same notebook means you immediately see *what* the MCP server is for. They share a tool-call mental model: a function with a clear input contract, an external API, graceful failure, and structured output.

**What to extract:**
- `engine/adapters/local_fs/mcp_server.py` — the local-fs MCP
- `engine/adapters/youtube/extractor.py` — the YouTube extractor (function, not a plugin class)
- `engine/adapters/youtube/__init__.py`
- `engine/adapters/_template/` — a starter skeleton for adding new source adapters
- `engine/prompts/youtube_summary.md` — versioned summary prompt
- `engine/prompts/orchestrator.md` (first draft, used in NB 07)
- Tests in `tests/adapters/test_youtube.py`

---

## Notebook 07 — Multi-agent orchestration with query decomposition and gap detection

**Name:** 07_orchestrator_qa
**Purpose:** Implement the §7.1 orchestrator + subagents pattern. Cover both ingest (write) and the *full* QA path including decomposition and knowledge-gap detection — the v0.4 additions that turn QA from "answer or fail" into a query-ingest loop.
**Exam relevance:** Architecture Patterns (orchestrator-subagent at both ingest and query time is exam-canonical).
**Design refs:** §7.1 agent responsibilities (especially the QA agent's three behaviours), §10 Scenario A (write), Scenario C (read), Scenario I (knowledge gap loop).
**Depends on:** NB 02, 05, 06.
**Estimated time:** 5–6 hours.

### Part A — Orchestrator basics (~1.5h)

- Orchestrator agent (Sonnet 4.6) with subagent tools: `spawn_ingest_agent(input)`, `spawn_qa_agent(question)`.
- Write path test: `"ingest /path/to/file.pdf"` → orchestrator dispatches to ingest agent → ingest returns a draft page → orchestrator returns confirmation with the page path.
- Trace visualization: print the agent tree (parent + subagents + tool calls) using `rich.tree`.
- Token attribution: per-agent usage breakdown. Confirms the orchestrator stays cheap and subagents do the heavy lifting.

### Part B — Single-question QA path (~1h)

- `"what did we decide about Q2 pricing?"` → orchestrator searches the local poc-wiki index → spawns QA subagent with relevant pages → QA returns answer with citations.
- Confirm citation integrity: every `[[page]]` in the answer corresponds to a real wiki path.

### Part C — Query decomposition (~1.5h)

- Decomposition prompt: classify whether a question is compound; if so, split into focused sub-queries. Returns a list of `SubQuery` objects.
- Run sub-queries in parallel via `asyncio.gather()`.
- Merge step: synthesize a final answer from the sub-query results, citing across all of them.
- Test with a compound question: `"what did we decide about Q2 pricing AND how does it affect the Apollo project?"` — observe two parallel retrievals, merged answer.
- Fallback: if decomposition itself fails (LLM returns malformed JSON), single-question path runs.
- A/B: compound question with vs without decomposition. Compare quality, latency, cost.

### Part D — Knowledge gap detection (~1h)

- Retrieval threshold logic: if results return fewer than N pages above relevance score, return a structured `KnowledgeGap` instead of a synthesized answer.
- `KnowledgeGap` includes: the failed query, what was retrieved, suggested next ingests (URLs or search queries the agent thinks would fill the gap).
- Test with a question the wiki can't answer: `"what's our exposure to the EU AI Act?"` against a wiki that doesn't cover it.
- Demonstrate the loop: gap → `marginalia add <suggested URL>` → re-ingest → re-query produces a real answer.

### Part E — Edge cases (~1h)

- Ambiguous input: `"tell me about pricing"` — orchestrator must decide ingest vs QA. Prompt-tune the routing.
- Subagent timeout: orchestrator's recovery behavior.
- Decomposition that returns a single sub-query (degenerate case): treated as single-question path.

**What to extract:**
- `engine/agents/orchestrator/main.py`
- `engine/agents/qa/single_question.py`
- `engine/agents/qa/decompose.py` — query decomposition logic
- `engine/agents/qa/gap_detection.py` — `KnowledgeGap` model + threshold logic
- `engine/prompts/qa_single.md`
- `engine/prompts/qa_decompose.md`
- `engine/prompts/qa_gap_signal.md`
- A short `engine/agents/README.md` describing the agent topology

---

## Notebook 08 — The lint pass: contradiction detection + archive with sync backlink rewrite

**Name:** 08_lint_archive
**Purpose:** Demonstrate the most demanding flow — Opus reasoning across the wiki, executing the §10 Scenario H archive with atomic backlink rewrite.
**Exam relevance:** Architecture Patterns, Prompt Engineering (deep CoT for hard reasoning).
**Design refs:** §10 Scenarios D and H, §16.1 Q4 resolution, §12 failure modes.
**Depends on:** NB 01 (models), 02–07 (need a populated PoC wiki).
**Estimated time:** 5–6 hours.

**Cells:**
- Set up a deliberately-seeded `poc/wiki/` with 8–12 pages: 2 entities, 4 source pages, 3 decisions (one of which contradicts another, which the lint should catch), 2 concepts.
- Stale detection: scan all pages for `last_synced > 14d`; produce a stale list.
- Contradiction detection (Opus 4.7): walk decisions and concepts, run a structured CoT prompt asking "do any of these contradict each other?". Compare to the seeded contradiction. Tune prompt until it catches the seed reliably.
- Archive simulation: pick one decision page; simulate its upstream Notion source returning 404.
  - Move the file from `knowledge/decisions/` to `knowledge/decisions/archived/`.
  - Walk all pages; rewrite every `[[knowledge/decisions/X]]` to `[[knowledge/decisions/archived/X]]` in both frontmatter `related[]` arrays and body markdown.
  - All changes happen in a single batch — verify atomicity.
- Generate `lint-report.md`: stale count, contradictions found, orphan pages, archive actions taken.
- Bonus: dry-run mode that produces the report without writing files.

**What to extract:**
- `engine/agents/lint/full_pass.py`
- `engine/utils/backlink_rewrite.py` — sync backlink rewriter
- `engine/utils/wiki_walker.py` — generic page-tree iteration with frontmatter parsing
- `engine/prompts/lint.md`

---

## Notebook 09 — SQLite job queue, worker, and retry semantics

**Name:** 09_job_queue
**Purpose:** Make ingest durable. Build the §7.5 job queue end-to-end so a 30-source batch can survive a kernel restart and resume.
**Exam relevance:** Architecture Patterns (durable workflows are exam-canonical).
**Design refs:** §7.5 job queue.
**Depends on:** NB 02 (the ingest function the worker calls).
**Estimated time:** 4–5 hours.

**Cells:**
- SQLite schema from §7.5: `jobs` table with `id`, `kind`, `payload`, `status`, `attempts`, `parent_id`, etc. Create + migrate.
- Job model in Pydantic; `JobStatus` enum (`pending|running|succeeded|failed|dead`).
- `enqueue(kind, payload) -> job_id` — append a row, return the ID immediately.
- Worker loop: `poll → mark running → execute → transition status`. Use `tenacity` for backoff (1m, 5m, 30m).
- Test: enqueue 5 ingest jobs; run the worker; observe rows transitioning. Kill the worker mid-batch; restart; confirm pending jobs resume from where they were.
- Retry semantics: simulate a transient failure (mock the ingest function to raise once, then succeed). Confirm the job retries and lands as `succeeded`.
- Dead jobs: simulate persistent failure. After `max_attempts`, status becomes `dead`. `marginalia jobs retry <id>` puts it back to `pending`.
- Fan-out: parent job spawns N child jobs (e.g. a "search for X" enqueues N URL-ingest children). Demonstrate `parent_id` linking.
- Concurrency: two worker processes with row-level locking via SQLite's transaction. Confirm jobs don't get picked up twice.
- Performance probe: enqueue 1000 dummy jobs, time the drain. Validates that SQLite is plenty fast for this workload.

**What to extract:**
- `engine/jobs/db.py` — schema + connection management
- `engine/jobs/queue.py` — `enqueue`, `claim_next`, `complete`, `fail`
- `engine/jobs/worker.py` — the worker loop with `tenacity` retry
- `engine/jobs/models.py` — Pydantic models for jobs
- Migration script in `scripts/init_jobs_db.sql`

---

## Notebook 10 — The 3-layer cache and `CACHE_VERSION` invalidation

**Name:** 10_cache_layers
**Purpose:** Make repeated work near-free. Build the §7.6 cache and prove the `CACHE_VERSION` discipline catches the silent-stale-cache bug.
**Exam relevance:** API & SDK Usage (Anthropic prompt cache), cost engineering.
**Design refs:** §7.6 caching.
**Depends on:** NB 02 (the cacheable analyze step).
**Estimated time:** 2–3 hours.

**Cells:**
- L1 cache: file-backed key-value store under `<wiki-root>/.wiki/cache/analysis/`. Key = `sha256(source) + CACHE_VERSION + sha256(prompt)`.
- Wrap NB 02's `analyze_source()` in a cache decorator. Re-run analyze on the same source; observe cache hit; confirm zero API tokens consumed on hit.
- L2 cache: same idea but for raw LLM responses keyed on `sha256(rendered_prompt) + model_id + CACHE_VERSION`.
- L3 demonstration: enable Anthropic's native prompt caching on the `marginaliaConfig` system prompt (it's stable, large, perfect candidate). Inspect response metadata for `cache_read_input_tokens`. Show real cost reduction.
- The bug demo: edit a prompt template *without* bumping `CACHE_VERSION`. Re-run analyze. Watch the agent serve a stale cached response from the *old* prompt. This is the failure mode the version constant prevents.
- Fix: bump `CACHE_VERSION`. Re-run. Cache miss → fresh call → correct output.
- `marginalia cache clear` and `marginalia cache stats` implementations.
- Cache-age policy: mark entries with `created_at`; optional GC for entries older than N days.
- Cost-tracking integration: every cache hit logs `cached: True` in the `cost_records` table NB 11 will introduce.

**What to extract:**
- `engine/cache/__init__.py` — `CACHE_VERSION` constant + clear public API
- `engine/cache/l1_analysis.py` — analysis cache
- `engine/cache/l2_responses.py` — LLM response cache
- `engine/cache/decorators.py` — `@cached_analyze` and `@cached_llm_call`
- `engine/cache/admin.py` — clear, stats, GC

---

## Notebook 11 — Audit DB, hooks, and the live `dashboard.md`

**Name:** 11_audit_hooks_dashboard
**Purpose:** Wire up the observability surfaces. Three small things, one notebook — they share a "the wiki instruments itself" theme.
**Exam relevance:** Architecture Patterns (observability), responsible AI (audit trails).
**Design refs:** §13 observability, §13.2 audit DB schema, §13.3 dashboard.md, §14 hooks.
**Depends on:** NB 09 (audit DB lives next to jobs DB), NB 10 (cost records flow from cache).
**Estimated time:** 3–4 hours.

### Part A — Audit DB (~1.5h)

- SQL schema from §13.2: `ingest_history`, `cost_records`, `audit_events`. Create.
- `AuditWriter` class with append-only methods: `record_ingest`, `record_cost`, `record_event`.
- Wire `record_ingest` into NB 02's ingest function. Wire `record_cost` into the LLM-call wrappers from NB 10. Wire `record_event` into NB 08's contradiction-detection cell.
- Run a small end-to-end ingest from NB 02 with the audit writer enabled. Show the rows that landed.
- CLI surface: implement `marginalia audit history`, `marginalia audit cost --days 7`, `marginalia audit events`. Each with `--json` for scripting.
- Demonstrate a query: "show me every ingest of customer-data sources in the last week" — pure SQL against `audit.db`.

### Part B — Hooks (~1h)

- Hook config from §14: `[hooks.on_ingest_complete]` block in a sample `config.toml`.
- `HookDispatcher` that reads config, finds executable, sends JSON context on stdin, captures exit code.
- Two example hooks:
  - **Non-blocking** (`notify-slack.sh`): bash + `jq` + `curl` — five lines.
  - **Blocking** (`gate-on-confidence.py`): rejects ingest if `confidence == "low"` and source is in a sensitive folder.
- Demonstrate the difference: blocking hook fails → ingest job fails → job moves to `failed` status. Non-blocking hook fails → ingest succeeds, hook failure logged but doesn't block.
- Timeout handling: hook that hangs gets killed at `timeout_s`.

### Part C — `dashboard.md` (~1h)

- Hand-write a `dashboard.md` with Dataview blocks (the example from §13.3).
- Open in Obsidian (manual step — note in the notebook for the reader).
- Take screenshots of the rendered output (orphans list, contradictions table, stale page count).
- Generator: a function that, given the wiki state, produces a fresh `dashboard.md` with current Dataview queries. Lets the dashboard evolve as new query needs arise.
- Tie back to the audit DB: a small Dataview-style table fed *not* from frontmatter but from `audit.db` — useful when you want consumption metrics that don't fit on individual pages.

**What to extract:**
- `engine/audit/db.py` — schema + connection
- `engine/audit/writer.py` — `AuditWriter` with append-only methods
- `engine/audit/queries.py` — the queries powering `marginalia audit *` commands
- `engine/hooks/dispatcher.py` — `HookDispatcher` class
- `engine/hooks/examples/notify-slack.sh`
- `engine/hooks/examples/gate-on-confidence.py`
- `scripts/dashboard_template.md` — copyable starter for new wikis

---

## After notebook 11

You have:
- **A working PoC** that ingests local files, URLs, PDFs, images, and YouTube videos; synthesizes across them; answers questions with citations including decomposition and gap detection; lints itself for contradictions and staleness; queues work durably; caches aggressively; logs to three audiences; and dispatches lifecycle hooks.
- **An `engine/` package** with extracted, testable functions for every flow.
- **A populated audit trail** — every action the agent took during the PoC is queryable in `audit.db`.
- **Cert-prep receipts:** worked examples of every exam-relevant pattern in your own code, in your own voice, against your own real data.

**Next moves (post-PoC):**
- Wire the `marginalia` CLI (Typer) on top of `engine/` — turn the notebook calls into commands.
- Wire GitHub Actions to run the orchestrator and lint flows on real PRs.
- Add the Notion MCP and exercise NB 06's pattern against a real workspace.
- Build the Slack adapter (custom MCP) — the second cert-valuable adapter after local-fs and YouTube.
- Add the `marginalia scaffold` agent (could be a 12th notebook if helpful, or extracted directly from NB 08's lint patterns).

**Cert prep alongside the build:**
- NB 01–03 cover ~40% of API & SDK and Prompt Engineering domains.
- NB 04 and 06 cover the bulk of Models & Capabilities and Tool Use.
- NB 05 and 07–08 cover Architecture Patterns thoroughly.
- NB 09–11 round out Architecture with durable workflows, caching, and observability — three areas the exam touches that aren't otherwise exercised by the build.
- Safety & Constitutional AI and Responsible AI domains are *not* exercised by the build — these still need separate study (AUP reading, Constitutional AI paper, refusal mechanics). Plan one dedicated week for them, ideally between NB 06 and NB 07 when you'll appreciate the break from coding.

You're not just building a project. You're building a project that doubles as your study guide.
