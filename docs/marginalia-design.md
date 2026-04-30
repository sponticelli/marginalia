# Marginalia — Design Document

**Status:** Draft v0.4
**Owner:** Sandro
**Last updated:** 2026-04-30

---

## 0. Changelog

- **v0.4 (2026-04-30)** — Added: SQLite-backed job queue (§7.5), three-layer cache with `CACHE_VERSION` invalidation (§7.6), `purpose.md` companion to `AGENTS.md` (§5.2, §6), `marginalia scaffold` recurring command (§9.6, §10 Scenario I), query decomposition (§7.1 QA), knowledge gap detection (§7.1 QA, §10 Scenario J), two-step ingest with `--analyse-only` (§7.1 ingest, §9.6), multi-wiki isolation (§5.4), audit DB schema (§13), `on_*_complete` hooks for extensibility (§14 customization), three log surfaces split by audience (§13), live `dashboard.md` via Dataview (§13). Filled in YouTube adapter spec with transcript+timestamps, executive summary, CJK-aware length, and `youtube: <topic>` search prefix (§8, §9.6).
- **v0.3 (2026-04-29)** — `marginalia` CLI with git-style staging; sync backlink rewrite on archive (Scenario H).
- **v0.2 (2026-04-29)** — Resolved 6 of 7 open questions: split-repo architecture, strict schema with retry, archive-on-deletion, hybrid `raw/` storage.
- **v0.1 (2026-04-29)** — Initial design.

---

## 1. Summary

A persistent, LLM-maintained company knowledge base. Sources flow in from Notion, Slack, Hex, Granola, GDrive, and YouTube via MCP servers. An agent reads each source, summarizes it, integrates it into a structured wiki of interlinked markdown pages with rich frontmatter, and commits the result to a GitHub repository. Humans curate and consume; the LLM does the bookkeeping that makes a knowledge base actually useful over time.

The wiki is not a RAG cache. It is a **compounding artifact** — synthesis, cross-references, and contradictions are computed once at ingest and kept current, not re-derived on every query.

---

## 2. Goals & Non-Goals

### Goals

- Capture decisions, discussions, and analyses across heterogeneous source systems into a single navigable corpus.
- Maintain freshness automatically — when upstream sources change, dependent pages update.
- Make provenance and confidence first-class: every claim traces to evidence with explicit authority weighting.
- Surface contradictions rather than hide them.
- Be consumable by both humans (browsing in GitHub or Obsidian) and other agents (via MCP / file API).
- Make institutional decisions auditable and recoverable months later.

### Non-Goals

- Replace the upstream systems. Notion, Slack, etc. remain the system of record for raw content.
- Real-time chat assistant. Query latency budget is seconds-to-minutes, not milliseconds.
- ACL enforcement at storage layer. Sensitive material is filtered at ingest; the repo itself uses GitHub permissions.
- Generic full-text search infrastructure. Index files + GitHub search cover MVP; a dedicated search service is v2.

---

## 3. Personas

| Persona | Goal | Touchpoint |
|---|---|---|
| **Curator** (Sandro) | Direct ingestion, ask questions, evolve the schema | Chat with agent, review PRs |
| **IC consumer** | Find answers without interrupting people | Browse GitHub, ask questions via Issue or Slack bot |
| **Decision authority** (CTO, founders) | Approve canonical decisions and concept pages | PR review via CODEOWNERS |
| **New hire** | Onboard without 1:1 dependency on team members | Browse README → entity pages |
| **External agent** | Consume wiki as input to other workflows | Read repo via MCP / git clone |
| **The Agent** | Ingest, synthesize, lint, and answer | GitHub Actions, on-demand chat |

---

## 4. User Stories

### P0 — MVP

**Curator**

- As a curator-developer, I want to maintain a local `raw/` folder of source files (markdown clips, PDFs, images, transcripts, screenshots) accessible to the agent via a local-filesystem MCP server — so that I can capture sources without depending on external APIs and start feeding the wiki on day one.
- As a curator-developer, I want a `marginalia` CLI with git-style staging (`marginalia add`, `marginalia status`, `marginalia ingest`) that accepts both file paths and URLs uniformly — so that capture is one command regardless of source type, and I control exactly what enters the wiki and when.
- As a curator, I want to share a Notion URL with the agent and have a source page, an updated index, and any affected entity pages produced as a single GitHub PR within five minutes — so that capture takes one action, not ten.
- As a curator, I want the agent to flag when a new source contradicts an existing claim — so that I can decide whether to supersede the prior page or record the disagreement.
- As a curator, I want every wiki page to carry frontmatter with `sources`, `confidence`, `last_synced`, and `status` — so that I can tell at a glance whether a page is canonical, stale, or speculative.

**IC consumer**

- As an IC, I want to ask "what did we decide about Q2 pricing?" and receive an answer that cites the specific decision page — so that I can verify the claim without trusting the agent blindly.
- As an IC, I want to land on a Person page and see every decision, meeting, and project they touched — so that I can understand context before pinging them.

**Decision authority**

- As a CTO, I want PRs that touch `knowledge/decisions/` to require my review — so that the wiki's authoritative record reflects leadership intent, not the agent's best guess.

### P1 — Soon after MVP

- As a curator, I want a nightly lint pass that flags stale pages, orphan pages, missing cross-references, and contradiction candidates — so that maintenance doesn't depend on me remembering.
- As a new hire, I want a `start-here.md` per team that points to the canonical pages they actually need — so that onboarding has a defined surface.
- As any user, I want filed Q&A to become permanent wiki pages — so that explorations compound rather than vanishing into chat history.
- As a curator, I want to ingest Hex dashboards as `metric` pages with definition, owner, and current value — so that metrics stop being orphaned dashboard URLs.

### P2 — Later

- As a curator, I want a small CLI/MCP search server over the wiki — so that the agent (and other tools) can retrieve pages by hybrid BM25/vector match instead of scanning indexes.
- As any user, I want subscriptions to entity pages — so that I'm notified when a project I care about gains a new decision.
- As an external agent, I want a published MCP server that exposes wiki pages as tools — so that downstream automations can read the wiki as structured input.

---

## 5. System Architecture

### 5.1 Layers

```
┌─────────────────────────────────────────────────────────────┐
│  Sources (read-only via MCP)                                │
│  Local FS · Notion · Slack · Hex · Granola · GDrive · ...   │
└─────────────────────────────────────────────────────────────┘
                          │  pull / webhook
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Agent layer  (orchestrator + subagents)                    │
│  Ingest · Synthesize · Lint · QA · Sync                     │
└─────────────────────────────────────────────────────────────┘
                          │  read / write
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Wiki repo (GitHub)                                         │
│  entities/ · knowledge/ · sources/ · _ops/                  │
└─────────────────────────────────────────────────────────────┘
                          │  consume
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Consumers: humans (browser, Obsidian), other agents (MCP)  │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Repo layout

```
company-wiki/
├── README.md
├── AGENTS.md                # how the LLM should behave (style, tone, terminology)
├── purpose.md               # what belongs in this wiki — scope declaration
├── dashboard.md             # live Dataview-rendered health view (orphans, contradictions, counts)
├── CODEOWNERS
├── index.md                 # global catalog
├── log.md                   # human-readable Markdown event log
├── lint-report.md           # auto-generated nightly
│
├── entities/                # the "who/what" — long-lived hubs
│   ├── index.md
│   ├── people/
│   ├── teams/
│   ├── projects/
│   ├── customers/
│   ├── competitors/
│   └── products/
│
├── knowledge/               # synthesized, LLM-authored
│   ├── index.md
│   ├── concepts/            # strategy, jargon, frameworks
│   ├── decisions/           # the crown jewels — CODEOWNERS gated
│   ├── meetings/            # synthesized notes
│   ├── metrics/             # definitions + interpretation
│   ├── analyses/            # filed-back deep dives
│   ├── qa/                  # filed-back answers
│   └── archived/            # pages whose upstream sources were deleted
│
├── sources/                 # immutable per-artifact summaries
│   ├── index.md
│   ├── notion/              # summary pages only — originals stay upstream
│   ├── slack/
│   ├── hex/
│   ├── granola/
│   ├── gdrive/
│   ├── youtube/
│   ├── web/                 # summary pages — original snapshot in raw/web/
│   ├── raw/                 # durable archive of originals with no upstream home
│   │   ├── pdfs/
│   │   ├── images/
│   │   ├── transcripts/
│   │   └── web/             # web clip snapshots (link-rot insurance)
│   └── archived/            # source pages whose upstream was deleted
│
└── _ops/
    └── schemas/             # JSON schemas for frontmatter validation
                             # (prompts and scripts live in the code repo)
```

### 5.2.1 Two-repo split

The wiki repo (above) is **content only**. The agents, prompts, and CI workflows live in a separate code repo:

```
marginalia/         # the machinery — separate repo
├── README.md
├── agents/                  # orchestrator, ingest, synth, lint, qa
│   ├── orchestrator/
│   ├── ingest/
│   ├── synthesis/
│   ├── lint/
│   └── qa/
├── prompts/                 # versioned per agent role
├── adapters/                # one per source type (notion, slack, hex, ...)
├── tools/                   # wiki.search, wiki.upsert_page, etc.
├── workflows/               # GitHub Actions definitions targeting wiki repo
└── scripts/                 # ops, budget caps, secret detection
```

Why split: the wiki should be readable, forkable, and redactable as pure content. Embedding agent prompts, model selection logic, and credentials inside it conflates two very different lifecycles — one that changes weekly (engine) and one that changes hourly (content). The split also makes external sharing tractable: a redacted fork of the wiki repo can go to a customer or auditor; the engine never leaves.

Information moves freely *within* the company through the wiki repo (subject to GitHub team permissions). For external sharing, the workflow is fork → run a redaction pass (strip `entities/customers/`, `entities/people/` external IDs, anything tagged `confidential: true`) → publish.

### 5.3 Sync model

**Push, not pull.** Sources flow into the repo via webhooks (where supported) and scheduled jobs (where not). The wiki is the cache; live systems are upstream. Pulling at query time would be RAG with extra steps and would defeat the compounding-artifact goal.

| Source | Mechanism | Latency target |
|---|---|---|
| Local FS | Curator drops file in `~/wiki-raw/` (transient inbox), triggers ingest. Originals without an upstream home are promoted to `sources/raw/` in the repo; files with an upstream home (e.g. exported Notion pages) are deleted from the inbox after ingest | On-demand / seconds |
| Notion | Webhook on update + nightly reconciliation | < 10 min |
| Slack | Manual ingest by curator (selective) | On-demand |
| Hex | Nightly polling | < 24 hr |
| Granola | Webhook on meeting close | < 5 min |
| GDrive | Webhook + nightly reconciliation | < 10 min |
| YouTube | Manual ingest | On-demand |

### 5.4 Multi-wiki isolation

The engine is wiki-agnostic. A single engine instance can serve multiple independent wikis — e.g. a personal research wiki and a company wiki side by side — by pointing at different content repos. Each wiki has its own:

- Content repo (`company-wiki`, `personal-research-wiki`, …)
- Local config at `<wiki-root>/.wiki/config.toml`
- Job queue (`<wiki-root>/.wiki/jobs.db`)
- Audit DB (`<wiki-root>/.wiki/audit.db`)
- Cache (`<wiki-root>/.wiki/cache/`)
- Server port if the HTTP API is exposed (7070, 7071, 7072, …)

The CLI selects which wiki to operate on with `marginalia use <name>` (set default) or `-w <name>` per command. Cross-wiki contamination is impossible at the storage layer — the engine reads/writes only inside the chosen wiki root.

This costs almost nothing to design in upfront and is hard to retrofit later. Even if Sandro starts with one wiki, the architecture supports adding more without refactoring.

---

## 6. Page Schema

### 6.1 Universal frontmatter

```yaml
---
title: Q2 2026 Pricing Restructure
type: decision                # entity | concept | source | decision | meeting | metric | qa | analysis
status: active                # draft | active | stale | archived | superseded
created: 2026-04-29
last_synced: 2026-04-29
confidence: high              # high | medium | low
sources:
  - ref: notion://abc123
    kind: notion_page
    captured: 2026-04-25
    authority: canonical      # canonical | corroborating | offhand
  - ref: slack://C123/1714000000
    kind: slack_thread
    captured: 2026-04-26
    authority: corroborating
owners: ["[[entities/people/sandro]]"]
tags: [pricing, gtm, q2-2026]
related:
  - "[[knowledge/concepts/plg]]"
  - "[[entities/projects/apollo]]"
supersedes: "[[knowledge/decisions/2025-q4-pricing]]"
contradicts: []

# Conditional fields
archived_date:                # set when status flips to archived
archived_reason:              # upstream-deleted | superseded | manual
validation_errors: []         # populated only when ingest agent could not satisfy strict schema
---
```

### 6.2 Page-type contracts

| Type | Required fields | Body convention |
|---|---|---|
| `entity` | `title`, `tags`, `owners` | TL;DR · Key facts · Active engagements · Backlinks |
| `concept` | `title`, `confidence` | Definition · Why it matters · Related concepts · Sources |
| `source` | All `sources[0]` fields, `last_synced` | TL;DR · Key extracts · Quotes (timestamped) · Generated links |
| `decision` | `owners`, `confidence`, `status` | Context · Alternatives considered · Decision · Rationale · Open questions |
| `meeting` | `sources[0]`, `owners` (attendees) | Summary · Decisions · Action items · Quotes |
| `metric` | `owners` | Definition · Owner · Current value · Dashboard link · Interpretation |
| `qa` | `sources` (cited pages) | Question · Answer · Citations · Confidence |
| `analysis` | `confidence`, `sources` | Question · Method · Findings · Caveats |

### 6.3 Linking and naming

- All wiki-internal links use namespaced paths: `[[entities/projects/apollo]]`, not `[[Apollo]]`. Eliminates collisions and gives Obsidian's graph view useful structure.
- Filenames are kebab-case, lowercase, no dates in the slug except for `decisions/` and `meetings/` where chronology matters.
- Each folder has an `index.md` listing pages and subfolders with one-line descriptions, page count, and last update timestamp.

### 6.4 Wiki-level configuration: `purpose.md` and `AGENTS.md`

Two top-level files configure how the agent behaves against this specific wiki. The split is deliberate:

**`purpose.md` — the *what*.** Declares the wiki's scope. The ingest agent reads it before deciding whether a source belongs in this wiki at all. Off-topic sources are rejected with a citation to `purpose.md` rather than silently ingested. Example body:

```markdown
# Wiki Purpose

This wiki captures decisions, projects, customer engagements, and internal
concepts for Acme Corp. Sources in scope:

- Internal Notion pages, Slack threads, Granola meetings
- Customer-facing decisions and product announcements
- Competitor research and market analysis
- Engineering architecture and runbook material

Out of scope (the agent should refuse these):
- Personal notes, journal entries, unrelated research
- HR records, payroll, anything in entities/people/* that isn't about
  professional context
- Vendor marketing material unless explicitly tied to a sourcing decision
```

**`AGENTS.md` — the *how*.** Style, terminology, and synthesis behavior. Highest-priority instruction source for every agent invocation. Example concerns: "always link customer names to `entities/customers/`," "use US English spelling," "never quote internal salary figures." This is where the wiki gets its voice.

The split matters because scope and style decisions have different review processes — `purpose.md` changes are strategic (CTO-level), `AGENTS.md` changes are editorial (curator-level). Keeping them in one file would conflate the two.

Both files are regenerated by `marginalia scaffold` (§9.6.1), but pages already linked from `index.md` are protected — scaffold updates the meta-files, not the content.

---

## 7. Agent Architecture

A single orchestrator routes work to specialized subagents. The pattern is exam-canonical and the right shape here: each subagent has a narrow context window, a focused prompt, and a clear output contract.

```
                              ┌──────────────────────┐
                              │     Orchestrator     │
                              │  (Claude Sonnet 4.6) │
                              └──────────┬───────────┘
        ┌──────────────┬─────────────────┼─────────────────┬──────────────┐
        ▼              ▼                 ▼                 ▼              ▼
  ┌──────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────────┐  ┌────────────┐
  │  Ingest  │  │  Synthesis   │  │  QA agent   │  │ Lint agent   │  │  Scaffold  │
  │  agent   │  │  agent       │  │ + decompose │  │              │  │  agent     │
  │ (2-step) │  │ (Sonnet 4.6) │  │ + gap-detect│  │ (Opus 4.7)   │  │(Sonnet 4.6)│
  └──────────┘  └──────────────┘  │(Sonnet 4.6) │  └──────────────┘  └────────────┘
   ┌──┴──┐                        └─────────────┘
   ▼     ▼
 [Hai] [Son]
analyze synth
```

All subagents read/write through the **job queue (§7.5)** and **3-layer cache (§7.6)**.

### 7.1 Agent responsibilities

**Orchestrator.** Receives the user request. Decides which subagents to invoke, in what order, with what inputs. Holds the session context but delegates heavy lifting. Composes the final PR description.

**Ingest agent — two-step.** Per source, ingest runs in two distinct phases:

1. **Analyze.** Extract entities, classify page type, summarize body, propose tags. Output is a structured analysis (no wiki changes yet). This step is *cacheable* — same source content (SHA-256) + same prompt version produces the same analysis.
2. **Synthesize.** Take the analysis, the synthesis hint, and relevant existing pages; produce the final page set with frontmatter, links, and updates to affected pages.

The split has three benefits. (a) The CLI's `marginalia add --analyse-only` flag can run step 1 and stop, giving you a preview of "what does the agent think this source is about?" before committing. (b) Step 1 is highly cacheable — re-ingesting the same PDF after editing prompts only re-runs the cheap second step. (c) Step 1 uses Haiku 4.5; step 2 uses Sonnet 4.6 — the model ladder follows the work ladder.

Output is strict-schema validated (§7.3.1).

**Synthesis agent.** Given a new source page (post-analysis) and the existing index/affected entity pages, decides what to update across the wiki. Generates patch sets (which page, what change, why). Sonnet 4.6 — needs reasoning over 10–20 pages of context.

**QA agent — with decomposition and gap detection.** Given a question and the index, retrieves relevant pages, reads them, composes an answer with citations. Three behaviors:

1. **Single-question path.** Standard retrieval → read → answer with citations. Sonnet 4.6.
2. **Query decomposition.** If the question is compound (LLM-classified — e.g. "what did we decide about pricing *and* how does it affect Apollo?"), the QA agent first calls a decomposition step that splits it into focused sub-queries, runs them in parallel, and merges the results before final synthesis. Falls back to single-question path if decomposition fails. Maps directly onto orchestrator-subagent at query time, not just ingest time.
3. **Knowledge gap detection.** If retrieval returns thin or empty results — fewer than N relevant pages, or top results below a relevance threshold — the QA agent doesn't just say "I don't know." It returns a structured gap signal: "this topic has weak coverage; suggested next ingests: [...]." Closes the loop between query and ingest. The curator can then run `marginalia add <suggested URL>` and re-query.

Escalates to Opus 4.7 if the question requires synthesizing 5+ pages or detecting contradictions.

**Lint agent.** Runs on a schedule. Reads the full wiki (or a partition), looks for stale claims, contradictions, orphans, missing cross-references, and freshness gaps. Opus 4.7 — this is where deep reasoning earns its cost.

**Scaffold agent.** New in v0.4. Periodically regenerates `index.md`, `purpose.md`, and `AGENTS.md` from the current wiki state. Pages already linked in `index.md` are protected — scaffold only updates the meta-files and adds new orphans into appropriate categories. Sonnet 4.6 by default. Run on demand (`marginalia scaffold`) or on a weekly cron.

### 7.2 Model selection rationale

| Task | Model | Why |
|---|---|---|
| Source summarization | Haiku 4.5 | High volume, narrow task, cost matters |
| Entity extraction | Haiku 4.5 | Few-shot prompt makes this a small model job |
| Cross-page synthesis | Sonnet 4.6 | Reasoning over 5–20 pages |
| Q&A with citations | Sonnet 4.6 | Balance of quality and cost |
| Contradiction detection | Opus 4.7 | Subtle, requires careful reading |
| Decision page drafting | Opus 4.7 | High-stakes content, low volume |
| Nightly lint | Opus 4.7 | Worth the spend, runs once per day |

This selection is logged in `engine/decisions/model-selection.md` (in the engine repo) and revisited as model capabilities and prices shift.

### 7.3 Prompting patterns

- **System prompt** = the agent's role contract (drawn from `engine/prompts/<agent>.md`). Versioned in the engine repo, not the wiki repo.
- **XML tags** isolate source content (`<source>`), prior context (`<existing_pages>`), and constraints (`<schema>`).
- **Few-shot examples** for entity extraction (3 worked examples covering edge cases — partial names, nicknames, ambiguous references).
- **Chain-of-thought** is required for synthesis and lint (`<thinking>` tag before `<output>`); forbidden for ingest (latency).
- **Structured output** enforced via strict JSON schema for frontmatter (see §7.3.1).

#### 7.3.1 Strict schema enforcement

Frontmatter is validated against a strict JSON schema (`_ops/schemas/<page-type>.json`). The agent must satisfy the schema; degraded output is not accepted. The retry pattern:

1. Agent generates frontmatter + body.
2. Validator runs against the page-type schema.
3. **On success:** page is committed to the working branch.
4. **On failure:** validation errors are fed back into the agent's context as a `<validation_errors>` block. Agent retries (up to 3 attempts).
5. **After 3 failed attempts:** page is committed with `status: draft` and `validation_errors: [...]` populated. The PR description flags it as "needs human attention." Lint will surface it in the next report.

The system never silently commits malformed pages. Either the agent produces a schema-compliant page, or a human is explicitly notified that one slipped through. This trades occasional latency for content reliability — the right tradeoff when the wiki is meant to be authoritative.

### 7.4 Tool use

Agents are exposed to a small set of tools beyond MCP source connectors:

- `marginalia.search(query, type?, limit?)` — index-backed search
- `marginalia.read_page(path)` — fetch a page with parsed frontmatter
- `marginalia.upsert_page(path, frontmatter, body)` — write to a working branch
- `marginalia.find_related(entity, depth=1)` — graph traversal
- `marginalia.open_pr(title, body, branch)` — propose changes for review
- `marginalia.lint_check(path?)` — run schema and link validation

Tool definitions live in `engine/tools/` (engine repo) and are loaded into every agent's system context. The wiki repo's `_ops/schemas/` only holds the page/frontmatter schemas — the contracts about content shape.

### 7.5 Context management

The wiki will outgrow any single context window quickly. Strategy:

- **Routing via index.** Every agent reads the relevant `index.md` first, then drills into specific pages. Avoids loading the whole wiki.
- **Frontmatter-only previews.** The orchestrator reads frontmatter (cheap) for many pages before deciding which bodies to load.
- **Streaming for synthesis.** Long syntheses stream so the orchestrator can intercept early signals.
- **Batch API for nightly jobs.** Re-sync and lint use the Batch API — half cost, async fits the use case.

### 7.5 Job queue

Every long-running operation — ingest, synthesis, lint, scaffold, resync — is dispatched to a SQLite-backed job queue rather than executed inline. Jobs are durable, resumable, and inspectable.

**Why a queue.** A 30-source batch ingest takes minutes. If the CLI process dies (terminal closed, laptop sleep, OS crash), an inline implementation loses the work and leaves the wiki half-updated. A queue makes ingestion a fire-and-forget operation: `marginalia ingest` returns a job ID, the worker chews through the queue, and `marginalia jobs list` shows progress. Crashes resume from the next pending job.

**Schema.** Single SQLite file at `<wiki-root>/.wiki/jobs.db`:

```sql
CREATE TABLE jobs (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,              -- 'ingest' | 'synthesis' | 'lint' | 'scaffold' | 'resync'
    payload      JSON NOT NULL,              -- input parameters (source ref, options, etc.)
    status       TEXT NOT NULL,              -- 'pending' | 'running' | 'succeeded' | 'failed' | 'dead'
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    created_at   TIMESTAMP NOT NULL,
    started_at   TIMESTAMP,
    completed_at TIMESTAMP,
    error        TEXT,                       -- last error message + traceback
    result       JSON,                       -- output (PR URL, page paths, etc.)
    parent_id    TEXT REFERENCES jobs(id)    -- for fan-out (web search → URL ingest jobs)
);
```

**State machine.**

```
pending → running → succeeded
                  ↘ failed → (retry) → running
                            ↘ dead (after max_attempts)
```

**Worker.** A single worker process (`marginalia worker`) polls `pending` jobs, marks them `running`, executes, transitions to `succeeded`/`failed`. Failed jobs go back to `pending` with backoff (1m, 5m, 30m); `dead` after `max_attempts`. The worker can be invoked manually or runs as a background daemon.

**CLI surface.** `marginalia jobs list`, `marginalia jobs status <id>`, `marginalia jobs retry <id>`, `marginalia jobs cancel`, `marginalia jobs purge --older-than 30` (see §9.6).

**Why this matters for the architecture exam.** Durable workflows with retry semantics is canonical exam material — it's the difference between "agent demo that works on stage" and "agent system that survives the night."

### 7.6 Caching

Three cache layers stack to make repeated work near-free:

| Layer | What it caches | Key | Storage |
|---|---|---|---|
| **L1: Analysis** | Output of ingest step 1 (analyze) per source | `sha256(source_content) + CACHE_VERSION + prompt_hash` | `<wiki-root>/.wiki/cache/analysis/` |
| **L2: LLM responses** | Raw model responses for any deterministic call (lint passes, frontmatter generation) | `sha256(rendered_prompt) + CACHE_VERSION + model_id` | `<wiki-root>/.wiki/cache/llm/` |
| **L3: Provider prompt cache** | Anthropic's native prompt caching for long stable system prompts (AGENTS.md, schemas) | Anthropic-managed | (server-side) |

**Invalidation.** Three triggers automatically bust the cache:

1. **Source content changes** — SHA-256 of source bytes shifts; cache miss; re-run.
2. **Prompt template changes** — bump `CACHE_VERSION` constant in `engine/cache.py` whenever you edit a prompt template. All cache entries with the old version are ignored (and eventually GC'd).
3. **Explicit `--force`** — `marginalia add --force <source>` skips L1 and L2 for that source.

`marginalia cache clear` wipes L1 and L2 entirely (L3 is server-side, expires on its own).

**Why `CACHE_VERSION` matters.** Without it, you edit a prompt, re-run ingest, and the agent serves a stale cached response from the *old* prompt — the bug is invisible until output drifts in production. Bumping `CACHE_VERSION` is the discipline that makes prompt edits actually take effect. Treat it like a lockfile: every prompt edit ships with a `CACHE_VERSION` bump in the same PR.

**Effect.** Re-running nightly lint on an unchanged wiki costs ~zero tokens. Re-ingesting a previously-ingested PDF (e.g. testing prompt changes) only re-runs synthesis, not analysis. Provider prompt cache cuts the 50%+ of every request that's stable system-prompt material.

---

## 8. Source Adapters

Each source type has a dedicated adapter handling: pull mechanism, normalization, type classification, and any source-specific quirks. Below is the per-type contract.

| Source | Page type produced | Notable quirks |
|---|---|---|
| Local FS | `source` (subtype varies by file kind) | Catch-all for anything not yet wired to a live MCP. Markdown ingests directly; PDFs go through text extraction; images go through a vision-capable model; transcripts treated as long-form text |
| Notion page | `source` (often mirrors as `concept` or `decision`) | Database vs. page distinction; nested page hierarchy |
| Slack thread | `discussion` (subtype of `source`) | Capture positions and dissent, not just consensus; timestamps matter |
| Hex dashboard | `metric` page | Snapshot the chart image + capture the SQL/calc; metric outlives the chart |
| Granola meeting | `meeting` page | Timestamped quotes are evidence for downstream decision pages |
| GDrive doc | `source` | Permissions matter — filter at ingest |
| YouTube | `source` | Transcript-only ingestion via `youtube-transcript-api` (no API key needed). Timestamped `[MM:SS]` prefixes preserved for citation. Captions required (auto-generated or manual); silent skip with warning on uncaptioned, private, or deleted videos. Page body is two sections: LLM-generated executive summary + full timestamped transcript. CJK-aware summary length (200 words Latin, 400 words CJK). |
| Web URL | `source` | Snapshot to `sources/raw/web/` to survive link rot |

The non-obvious one is Slack: flattening a thread into a summary loses the signal. The adapter captures structure — who said what, what positions emerged, where dissent surfaced — and surfaces that in the body.

The **Local FS adapter** plays a special role. It is the lowest-friction capture path (drop a file, trigger ingest) and the catch-all for source types not yet supported by a dedicated MCP. It uses a generic local-filesystem MCP server pointed at `~/wiki-raw/` (path configurable). The adapter dispatches by file extension to the right ingest sub-flow:

| Extension | Sub-flow |
|---|---|
| `.md`, `.txt` | Direct text → ingest agent |
| `.pdf` | Text extraction first, then ingest agent; if extraction fails (scanned PDF) → vision pass |
| `.png`, `.jpg` | Vision-capable model describes content → ingest agent |
| `.srt`, `.vtt` | Strip timestamps for synthesis, retain for citation |
| `.json`, `.csv` | Treat as data; produce a `source` page summarizing the dataset |

---

## 9. GitHub Integration

### 9.1 Branching and PR conventions

- The agent never commits to `main`. Every operation opens a branch named `agent/<op>/<date>-<slug>`, e.g. `agent/ingest/2026-04-29-pricing-meeting`.
- Single-source ingests produce one PR. Bulk ingests produce one PR per source unless the curator opts into batching.
- PR descriptions use a structured template: summary, files changed (with rationale per file), entities touched, contradictions flagged, suggested follow-ups.

### 9.2 CODEOWNERS

```
/knowledge/decisions/   @cto @founders
/knowledge/concepts/    @cto
/entities/customers/    @gtm-lead
/_ops/                  @sandro
*                       @sandro
```

The agent drafts; the right humans sign. Authority is enforced at the GitHub layer, not by trusting the agent.

### 9.3 Auto-merge policy

A reasonable split that keeps reviewers from drowning:

- **Auto-merge** after CI passes: `sources/*` pages, `index.md` updates, `log.md` appends.
- **Require 1 reviewer**: `entities/*`, `knowledge/concepts/*`, `knowledge/qa/*`, `knowledge/analyses/*`.
- **Require CODEOWNERS approval**: `knowledge/decisions/*`, anything in `_ops/`.

This is one of the genuinely debatable calls — see Open Questions.

### 9.4 GitHub Actions

| Workflow | Trigger | Purpose |
|---|---|---|
| `validate.yml` | Every PR | Frontmatter schema, broken links, naming conventions |
| `ingest.yml` | `workflow_dispatch`, webhook | On-demand or webhook-driven ingest |
| `lint.yml` | Nightly cron | Lint pass, contradiction scan, freshness check |
| `resync.yml` | Nightly cron | Re-pull sources where `now - last_synced > 7d` |
| `qa.yml` | Issue with `question` label | Draft a `qa/` PR answering the issue |

### 9.5 Issues and Discussions

- **Issues with `question` label** become the question queue. Filing one triggers `qa.yml`, which drafts a `knowledge/qa/` page and links it back. Questions become permanent assets.
- **Discussions** host live debate before consensus. Once resolved, the agent files a `decisions/` PR summarizing.

### 9.6 The `marginalia` CLI

The curator's primary capture interface. Lives in the engine repo, operates on the wiki repo via git. Modeled on git's staging semantics — capture is intentional, not automatic.

#### Commands

**Capture and ingest:**

| Command | Purpose |
|---|---|
| `marginalia add <path-or-url>` | Stage a local file or URL for ingestion. URL dispatch by pattern (see below). |
| `marginalia add --analyse-only <path-or-url>` | Run only step 1 of ingest (analyze) and print the result. No staging, no commit. Preview before commit. |
| `marginalia add --force <path-or-url>` | Skip L1/L2 cache for this source. |
| `marginalia status` | Show staged items with previews (title, source type, size). |
| `marginalia unstage <ref>` | Remove a staged item. |
| `marginalia ingest [-m "<hint>"]` | Enqueue all staged items as ingest jobs. Returns a parent job ID immediately. Optional `-m` passes a synthesis hint. |
| `marginalia ingest --batch <folder>` | Enqueue every file in a folder as a separate ingest job. |
| `marginalia ingest --file <manifest>` | Enqueue every line of a manifest file (paths or URLs). |

**Query:**

| Command | Purpose |
|---|---|
| `marginalia ask "<question>"` | Run QA agent. Returns answer with citations to chat; does *not* persist by default. |
| `marginalia ask --save "<question>"` | Same, but commits the answer as a `knowledge/qa/` page via PR. |
| `marginalia search <query>` | Local index-backed search (no LLM). Faster, cheaper. |

**Maintenance:**

| Command | Purpose |
|---|---|
| `marginalia lint [--scope <area>]` | Enqueue a lint job. `--scope contradictions` limits to one check. |
| `marginalia scaffold` | Regenerate `index.md`, `purpose.md`, `AGENTS.md` from current wiki state. Pages already linked are protected. |
| `marginalia resync <page>` | Force re-fetch from upstream and update the page. |
| `marginalia use <wiki-name>` | Set default wiki (no `-w` needed afterward). |

**Jobs:**

| Command | Purpose |
|---|---|
| `marginalia jobs list [--status <s>]` | List jobs (most recent first). Filter by `pending`, `running`, `failed`, `dead`, `succeeded`. |
| `marginalia jobs status <id>` | Detail for one job: status, attempts, error, result. |
| `marginalia jobs retry <id>` | Move a `dead`/`failed` job back to `pending`. |
| `marginalia jobs cancel [--yes]` | Cancel all `pending` jobs. Confirms unless `--yes`. |
| `marginalia jobs purge --older-than <days>` | Delete completed/dead records older than N days. |
| `marginalia worker` | Run the queue worker in the foreground. Daemonize separately if needed. |

**Cache:**

| Command | Purpose |
|---|---|
| `marginalia cache clear` | Wipe L1 and L2 caches. |
| `marginalia cache stats` | Hit rate, entry count, total size. |

**Audit & ops:**

| Command | Purpose |
|---|---|
| `marginalia audit history [-n N]` | Last N ingests: source, page, tokens, timestamp. |
| `marginalia audit cost [--days N]` | Token usage totals + daily breakdown. |
| `marginalia audit events` | Audit events: contradictions, archive moves, cost-gate triggers. |
| `marginalia log [-n N]` | Show last N entries from `log.md` (human-readable). |
| `marginalia status` | (When no items staged) Wiki health: page count, queue depth, cache hit rate. |

#### URL pattern dispatch

`marginalia add` is the universal capture verb. The CLI inspects the input and routes to the right adapter:

| Input pattern | Adapter | Behaviour |
|---|---|---|
| `*.md`, `*.txt`, `*.pdf`, `*.png`, etc. | Local FS | File staged in place; promoted to `sources/raw/` on ingest |
| `https://notion.so/...` | Notion | Fetched via MCP; staged as Notion source |
| `https://app.slack.com/.../p<ts>` | Slack | Thread fetched via MCP; staged as discussion |
| `https://*.hex.tech/...` | Hex | Dashboard pulled; staged as metric candidate |
| `https://granola.ai/.../<id>` | Granola | Meeting transcript pulled |
| `https://youtube.com/...`, `https://youtu.be/...`, `https://youtubekids.com/...` | YouTube | Transcript pulled via `youtube-transcript-api`; LLM-generated executive summary prepended; full timestamped transcript preserved for citation |
| `https://docs.google.com/...` | GDrive | Doc pulled via MCP |
| Anything else `https://...` | Web | Page fetched + snapshotted to `sources/raw/web/` |
| `youtube: <topic>` | YouTube via Web search | Search YouTube via Tavily, ingest top N results as YouTube sources |
| `search for: <topic>`, `find on the web: <topic>` | Web search | Tavily search; each result URL ingested through its matching adapter (YouTube URLs route to the YouTube adapter automatically) |

This unifies the capture path across all source types into a single command. Prefix patterns (anything matching `^<keyword>: `) extend the dispatch beyond URL matching — the prefix is stripped before being passed to the adapter. Chat ingestion ("ingest this URL") delegates to the same dispatcher under the hood — the CLI is the stable contract; chat is a friendly skin on top.

#### 9.6.1 The `marginalia scaffold` command

Regenerates the three meta-files from current wiki state:

- **`index.md`** — Re-categorizes pages and updates per-folder summaries. Pages already linked from the *current* index are protected; orphans are categorized into appropriate sections.
- **`purpose.md`** — Refreshes the scope declaration based on what's actually in the wiki. Useful when scope has drifted (e.g., a project wiki started ingesting personal research). The agent surfaces "your purpose says X, but you've ingested 30 pages about Y" as a flag.
- **`AGENTS.md`** — Updates terminology (new domain-specific jargon detected in pages), refines style guidelines based on observed patterns.

Never touched: `config.toml`, `dashboard.md`, any page already linked in `index.md`. Scaffold is additive and meta-level; it doesn't rewrite content.

Run on demand (`marginalia scaffold`) or schedule weekly (`marginalia schedule add --op scaffold --cron "0 4 * * 0"`).

#### Staging mechanics

- Staging metadata lives at `~/.wiki/staging.json`. Records: input ref, resolved adapter, fetched preview, staged-at timestamp, optional message.
- For URL-based adds, the snapshot is fetched immediately (fail-fast) and cached at `~/.wiki/cache/<hash>` until ingest.
- `marginalia ingest` enqueues each staged item as a job; the worker processes them with retry. Per-source PRs by default; bundled into one PR if `-m` hint suggests they're thematically related.
- If an individual item fails its strict-schema validation after retries, that item lands as `status: draft` (per §7.3.1) and shows up in `marginalia jobs list --status failed` for inspection.
- After successful ingest, the staging area is cleared and the local cache is purged for items that have been promoted to `sources/raw/`.

---

## 10. Detailed Scenarios

### Scenario A — Ingesting a Notion page (happy path)

**Trigger:** Curator pastes `https://notion.so/.../q2-pricing-restructure` into chat with "ingest this".

1. Orchestrator (Sonnet 4.6) receives the request, recognizes a Notion URL, routes to ingest agent with the URL and the Notion MCP tool available.
2. Ingest agent (Haiku 4.5) calls `notion.fetch_page(id)`. Receives title, body, metadata, last-edited timestamp.
3. Ingest agent extracts entities (people mentioned, projects, customers), classifies as `decision` candidate, summarizes body to ~300 words, generates frontmatter against the source schema.
4. Output returns to orchestrator. Orchestrator calls `marginalia.search(entities)` to find related pages — finds `entities/projects/apollo`, `knowledge/decisions/2025-q4-pricing`.
5. Orchestrator hands off to synthesis agent (Sonnet 4.6) with the new source draft + the related pages. Synthesis agent decides:
   - Create `sources/notion/2026-04-25-q2-pricing-restructure.md`
   - Update `entities/projects/apollo.md` (append to "Active engagements")
   - Create `knowledge/decisions/2026-q2-pricing.md` referencing the source as `authority: canonical`
   - Set `supersedes: [[knowledge/decisions/2025-q4-pricing]]` on the new decision; set `status: superseded` on the old one
   - Append entry to `log.md`
   - Update `entities/projects/index.md`, `knowledge/decisions/index.md`, `sources/notion/index.md`
6. Orchestrator opens `agent/ingest/2026-04-29-q2-pricing` PR with structured description listing all 7 file changes and rationale.
7. CODEOWNERS routes to CTO + founders due to `decisions/` touch. Curator is auto-tagged for visibility.

**Expected wall time:** 90–180 seconds for ingest+synth, plus PR review.

### Scenario B — Ingesting a Slack thread that contradicts a decision

**Trigger:** Curator: "ingest #eng-platform thread from this morning, it's about the pricing thing."

1. Slack adapter pulls the thread. Eight messages, three participants, dissenting views.
2. Ingest agent classifies as `discussion` (Slack subtype). Captures positions per participant, not a flattened summary.
3. Synthesis agent reads the new source against the just-merged `2026-q2-pricing.md` decision page. Detects:
   - Two engineers raised concerns about implementation feasibility that the decision page doesn't acknowledge.
   - One engineer cites a customer escalation the decision page doesn't reference.
4. Synthesis agent writes the source page, then proposes adding `contradicts: [[sources/slack/...]]` and a "Dissent" section to the decision page. **Does not** flip the decision's status — that's a human call.
5. PR description leads with: "⚠️ This source surfaces dissent against an active decision." CODEOWNERS routes to CTO.
6. CTO reviews. Three possible resolutions:
   - Acknowledge dissent, decision stands. Merge as-is; the decision now carries explicit dissent metadata.
   - Reopen the decision. CTO closes the PR, opens a Discussion, the decision moves to `status: under-review`.
   - Supersede. CTO accepts the contradiction as decisive; new decision page is drafted.
7. Whichever path: the disagreement is now in the wiki, not just in Slack history.

This scenario is the project's whole reason to exist. Companies hemorrhage *why* — this captures it.

### Scenario C — IC asks a question via GitHub Issue

**Trigger:** IC opens issue: "What did we decide about Q2 pricing and why?" with `question` label.

1. `qa.yml` workflow fires. Orchestrator (Sonnet 4.6) receives the question.
2. Orchestrator calls `marginalia.search("Q2 pricing")` against `knowledge/decisions/index.md` and related indexes. Top hit: `knowledge/decisions/2026-q2-pricing.md`.
3. QA agent (Sonnet 4.6) reads the decision page + linked source pages + `entities/projects/apollo.md`.
4. QA agent composes answer: TL;DR, what was decided, rationale, who decided, sources cited inline.
5. Orchestrator opens PR creating `knowledge/qa/2026-04-29-q2-pricing-rationale.md` with the answer; links the PR in the issue.
6. After merge: the issue is closed; the answer is now a permanent wiki page that future searches will find directly.

**Why this matters for the cert exam:** this is the canonical RAG-meets-tool-use pattern, with citations as a first-class output requirement.

### Scenario D — Nightly lint discovers contradiction + stale page

**Trigger:** `lint.yml` cron at 03:00 UTC.

1. Lint agent (Opus 4.7) reads `index.md`, scans frontmatter for all pages where `now - last_synced > 14d`, builds a stale candidate list.
2. For each non-source page, lint agent reads the page + its `sources[]` references, calls each source's adapter to check upstream `last_modified`. Finds 12 pages where upstream changed.
3. Lint agent runs a contradiction scan: walks `decisions/` and `concepts/`, looks for claims that newer source pages would invalidate. Finds 1 likely contradiction in `concepts/onboarding-flow.md`.
4. Lint agent generates `lint-report.md` with sections: Stale Pages (12), Likely Contradictions (1), Orphan Pages (3), Missing Cross-References (suggested 7), Concepts Lacking Their Own Page (4).
5. Lint workflow opens PR updating `lint-report.md` and tagging Sandro for review.
6. Sandro browses the report, decides which items to action; high-confidence stale pages get auto-resync PRs queued.

### Scenario E — New hire onboarding

**Trigger:** New eng hire, day one. Manager points them at the wiki repo.

1. Hire opens `README.md` → directed to `start-here/engineering.md`.
2. `start-here/engineering.md` is a curated index pointing to: their team page, top 5 active projects, the engineering principles concept page, the on-call decision, the deployment runbook source.
3. Hire clicks `entities/teams/platform.md`. Sees the team's people, active projects, recent decisions (last 30 days, autopopulated by Dataview-style query if Obsidian is used, by static index otherwise).
4. Hire clicks `entities/projects/apollo.md`. Sees TL;DR, active decisions, recent meetings, owner, key sources.
5. Hire arrives at concrete artifacts within 3 clicks. No 1:1 ambush required.

This is the lowest-glamour but possibly the highest-value scenario. New-hire ramp time is where wikis pay back.

### Scenario F — Multi-modal: Hex dashboard ingest

**Trigger:** Nightly Hex polling job.

1. Hex adapter pulls the dashboard via Hex MCP. Receives chart image (PNG), SQL query, computed values, last-refreshed timestamp.
2. Ingest agent (Haiku 4.5, but switched to a vision-capable model for this path) examines the chart image, extracts the headline trend in plain language ("MRR up 12% MoM, churn flat at 2.1%").
3. Ingest agent generates a `metric` page if one doesn't exist for this dashboard, or updates the existing one. Frontmatter records: definition (from the SQL), owner, current value, dashboard link.
4. The chart PNG is saved alongside the page in `sources/hex/<dashboard>/charts/2026-04-29.png` so the visual is recoverable even if the dashboard is later deleted.
5. If the value crossed a watch-threshold defined in the metric page's frontmatter, lint will flag it on the next pass.

### Scenario G — Local raw folder ingest (the day-one path)

**Trigger:** Sandro saves three artifacts during a research session into `~/wiki-raw/`:
- A web-clipped article (`2026-04-29-platform-engineering-podcast.md`)
- A PDF whitepaper (`spotify-model-paper.pdf`)
- A screenshot of an architecture diagram (`apollo-arch-v2.png`)

Sandro stages each via the CLI, mixing local files and a URL:

```bash
$ wiki add ~/Downloads/spotify-model-paper.pdf
  Staged: spotify-model-paper.pdf (PDF, 2.1MB) — adapter: local-fs

$ wiki add ~/Desktop/apollo-arch-v2.png
  Staged: apollo-arch-v2.png (image, 480KB) — adapter: local-fs

$ wiki add https://www.example.com/blog/platform-engineering-podcast
  Staged: "What we learned building an internal platform" — adapter: web

$ wiki status
  3 items staged for ingest:
    1. spotify-model-paper.pdf (local-fs, PDF)
    2. apollo-arch-v2.png (local-fs, image)
    3. example.com/.../platform-engineering-podcast (web)

$ wiki ingest -m "platform engineering research session"
  Opening PR agent/ingest/2026-04-29-research-batch...
```

Behind the scenes:

1. The CLI's three `marginalia add` calls each route to their adapter. URL is fetched immediately (fail-fast); previews shown back to Sandro. Metadata stored in `~/.wiki/staging.json`.
2. `marginalia ingest` invokes the orchestrator (Sonnet 4.6) with all three staged items + the synthesis hint.
3. Orchestrator dispatches each to the right ingest sub-flow:
   - **Markdown clip / web fetch** → ingest agent (Haiku 4.5) directly
   - **PDF** → text extraction → ingest agent; on failure, fallback to vision pass page-by-page
   - **PNG** → vision-capable model produces a structured description → ingest agent
4. Each ingest agent produces a draft source page with strict-schema frontmatter. Schema validation runs; agent retries on failure (§7.3.1).
5. Synthesis agent receives all three drafts together with the hint, reasons across them: the podcast article and the Spotify paper both touch platform engineering — creates a new `knowledge/concepts/internal-platforms.md` page citing both. The architecture diagram updates `entities/projects/apollo.md`.
6. Orchestrator opens one PR `agent/ingest/2026-04-29-research-batch` with the full change set.
7. **Persistence step.** Originals committed to `sources/raw/` in the repo (PDF → `sources/raw/pdfs/`, PNG → `sources/raw/images/`, web snapshot → `sources/raw/web/`) — none have a durable upstream home. Staging cleared.
8. Log entry appended; `last_ingest_ts` advanced.

**Why this scenario matters most for MVP:** it requires zero external integrations beyond a local FS and `web_fetch`. Once the CLI, the orchestrator, and the ingest agent exist, Sandro can start feeding the wiki on day one. Notion/Slack/Hex adapters extend this same `marginalia add` interface — they don't replace it.

---

### Scenario H — Archive on upstream deletion (sync backlink rewrite)

**Trigger:** Nightly `resync.yml` finds that `notion://abc123` (the source backing `knowledge/decisions/2026-q1-vendor-choice.md`) returns 404 — the page was deleted in Notion.

1. Sync agent confirms the 404 with a retry to rule out transient errors.
2. Sync agent calls `marginalia.find_related(decision-page, depth=1)` to enumerate every page that links to `2026-q1-vendor-choice.md`. Finds 8 inbound references across 5 pages (entities, an analysis, two other decisions).
3. Sync agent moves the page: `knowledge/decisions/2026-q1-vendor-choice.md` → `knowledge/decisions/archived/2026-q1-vendor-choice.md`. Sets `status: archived`, `archived_date: 2026-04-29`, `archived_reason: upstream-deleted`.
4. **Sync backlink rewrite.** Sync agent walks all 5 pages with inbound references, rewrites `[[knowledge/decisions/2026-q1-vendor-choice]]` → `[[knowledge/decisions/archived/2026-q1-vendor-choice]]` in both frontmatter `related[]` arrays and body markdown. Updates each page's `last_synced` timestamp.
5. Updates `knowledge/decisions/index.md` and `knowledge/decisions/archived/index.md`.
6. Opens one PR with all changes (the archived page + 5 backlink rewrites + 2 indexes). PR description leads with: "📦 Archiving `2026-q1-vendor-choice` — upstream Notion page deleted. 8 backlinks rewritten across 5 pages."
7. Because this touches `decisions/`, CODEOWNERS routes to CTO for review. The atomic PR makes the full impact visible — no partial state, no broken links left in `main`.

---

### Scenario I — Knowledge gap → ingest → re-query loop

**Trigger:** Sandro runs `marginalia ask "What's our exposure to the new EU AI Act?"`.

1. QA agent runs decomposition; question is single-topic, not split.
2. QA agent calls `marginalia.search("EU AI Act")`. Returns 2 hits, both low-relevance (mention "AI Act" in passing in unrelated meeting notes).
3. Knowledge gap detection fires — fewer than 3 relevant pages, all below relevance threshold. Instead of synthesizing a weak answer, QA agent returns a structured gap signal:

```
Coverage: weak (2 pages, none directly about EU AI Act)

Suggested next ingests:
  - https://artificialintelligenceact.eu/
  - https://www.europarl.europa.eu/.../ai-act-overview
  - Internal: any compliance memos from legal team (Notion search recommended)

Re-run `marginalia ask` after ingest.
```

4. Sandro reviews suggestions, runs `marginalia add https://artificialintelligenceact.eu/` and `marginalia add https://www.europarl.europa.eu/...`. Both stage; `marginalia ingest` enqueues the jobs.
5. Worker processes the jobs over the next 90 seconds. Two new `sources/web/` pages land. Synthesis agent creates a new `knowledge/concepts/eu-ai-act.md` page citing both.
6. Sandro re-runs `marginalia ask "What's our exposure to the new EU AI Act?"`. This time retrieval returns the new concept page with high relevance; QA agent synthesizes a real answer with citations.
7. Optional: `marginalia ask --save "..."` files the answer permanently as a `knowledge/qa/` page so future searches surface it directly.

**Why this scenario is the unlock.** The standard query-only mental model treats unanswered questions as failures. This scenario treats them as *signals* — the wiki tells you what to feed it. Each gap-driven ingest cycle makes the wiki denser; future queries about the same topic don't need the loop.

---

### Scenario J — Periodic scaffold refresh

**Trigger:** Weekly cron (`0 4 * * 0`) fires `marginalia scaffold`.

1. Scaffold agent (Sonnet 4.6) reads current `index.md`, `purpose.md`, `AGENTS.md`, and walks the wiki for all pages.
2. Builds three parallel reports:
   - **Index drift.** 14 pages exist that aren't linked from `index.md` (orphans by index, not necessarily by graph). Categorizes each into the right section.
   - **Scope drift.** Detects 5 pages tagged `personal-research` that don't fit `purpose.md`'s declared scope of "company decisions and projects." Flags but doesn't move them.
   - **Terminology drift.** Notices 8 mentions of "Apollo Project" with mixed capitalization across pages; AGENTS.md should standardize.
3. Generates an updated `index.md` (orphans now linked, categorized), refreshed `purpose.md` (scope unchanged but explicitly notes the personal-research drift as out of scope), updated `AGENTS.md` (adds "Always write `Apollo` in title case").
4. Opens PR `agent/scaffold/2026-05-04-weekly` with the three updated meta-files. PR description summarizes: "14 orphans linked, 5 scope-drift flags, 1 terminology rule added."
5. **Pages already linked are not touched.** Scaffold is meta-level; it doesn't rewrite content.
6. Sandro reviews. The scope-drift flag prompts a separate `marginalia ingest` decision — ingest those pages into a personal-research wiki, archive them, or update `purpose.md` to widen scope.

This is the rare case where the wiki gets *better* without anyone adding new content. Scaffold is the wiki's grooming pass.

---

## 11. Security & Governance

**Philosophy: information moves freely *inside* the company; redaction happens at the edge.** The wiki repo is broadly readable to all employees with GitHub access — that openness is the point. Sensitive material is filtered at *ingest*, not at storage. For external sharing (customers, auditors, partners), a separate redaction pass runs on a fork.

- **PII handling.** Slack adapter hashes user IDs by default; un-hashing requires explicit curator opt-in per channel. People-page mappings live in `entities/people/` and are the only place names appear.
- **ACL passthrough at ingest.** GDrive and Notion adapters check the source's permissions; private documents produce a stub page with `access_restricted: true` and the actual content omitted.
- **Confidentiality tagging.** Frontmatter supports `confidential: true` for pages that should be stripped during external redaction. Used sparingly — most things shouldn't need it.
- **Secrets.** Never ingested. The validator rejects PRs whose diffs match common secret patterns; allow-list lives in the engine repo at `engine/scripts/secret-patterns.json`.
- **Repo access.** Standard GitHub team permissions. The wiki repo is private; downstream consumers go through GitHub's auth.
- **External sharing pipeline.** Fork wiki repo → run redaction pass (strip `entities/customers/`, `entities/people/` external refs, `confidential: true` pages) → publish to target audience.
- **Audit.** `log.md` is append-only and signed commits are required for changes touching CODEOWNERS-gated paths.

---

## 12. Failure Modes & Mitigations

| Failure mode | Mitigation |
|---|---|
| Ingest agent hallucinates an entity that doesn't exist | Synthesis agent validates entity references against `entities/*/index.md` before writing; unknown entities surface as a separate "needs review" PR |
| Source MCP returns rate limit / 429 | Adapter has exponential backoff; hard failures surface as `status: failed-ingest` source pages with retry instructions |
| Two ingests race and both touch the same entity page | PR-based workflow serializes — second PR rebases or surfaces a merge conflict for human resolution |
| Frontmatter fails strict schema | Agent retries up to 3x with validation errors in context (see §7.3.1); after 3 failures, page committed with `status: draft` and `validation_errors` populated for human review |
| Stale wiki nobody reads | Observability metrics (section 13) measure consumption; a wiki nobody reads is a problem signal, not just a missed metric |
| Agent confidently writes wrong synthesis | All synthesis carries `confidence` field and citations; lint flags low-citation high-confidence claims for review |
| Cost runaway from too many ingests | Budget cap per day in `engine/scripts/budget.py`; ingest agent fails closed when exceeded |
| Upstream source deleted | Sync agent detects on next pass; moves wiki page to `<folder>/archived/`, sets `status: archived` and `archived_reason: upstream-deleted`. Backlinks across the wiki are rewritten **synchronously in the same PR** (atomic — no broken-link window in `main`). See Scenario H. |

---

## 13. Observability

Three concerns — debugging, audit, and at-a-glance health — get three different surfaces. Each is shaped for its audience.

### 13.1 Three log surfaces

| Surface | Location | Format | Audience | Use |
|---|---|---|---|---|
| **`log.md`** | `<wiki-root>/log.md` (in the wiki repo, browsable in Obsidian) | Human-readable Markdown | Curator, IC consumers | "What did the agent do this week?" — chronological, narrative, opens in any editor |
| **`engine.log`** | `<wiki-root>/.wiki/logs/engine.log` (rotating) | JSON lines (one record per line) | Ops, debugging | `tail -f \| jq` for real-time inspection; structured fields for filtering |
| **`audit.db`** | `<wiki-root>/.wiki/audit.db` | SQLite (append-only) | Compliance, post-hoc queries | "Show me every ingest of customer-data sources in Q1" — queryable history |

Most projects pick one and force every audience to use it. Splitting by audience costs almost nothing and makes each surface useful. `log.md` lives *inside* the wiki repo so it travels with the content (and renders in Obsidian).

### 13.2 Audit DB schema

Three tables in `audit.db`:

```sql
CREATE TABLE ingest_history (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL,
    source_ref   TEXT NOT NULL,           -- 'notion://abc', '/path/to/file.pdf', etc.
    source_hash  TEXT NOT NULL,           -- SHA-256 of source content
    page_paths   JSON NOT NULL,           -- list of wiki pages created/updated
    tokens_in    INTEGER NOT NULL,
    tokens_out   INTEGER NOT NULL,
    cost_usd     REAL NOT NULL,
    duration_ms  INTEGER NOT NULL,
    timestamp    TIMESTAMP NOT NULL
);

CREATE TABLE cost_records (
    id           TEXT PRIMARY KEY,
    job_id       TEXT,
    agent        TEXT NOT NULL,           -- 'ingest' | 'synthesis' | 'qa' | 'lint' | 'scaffold'
    model        TEXT NOT NULL,           -- 'haiku-4-5' | 'sonnet-4-6' | 'opus-4-7'
    tokens_in    INTEGER NOT NULL,
    tokens_out   INTEGER NOT NULL,
    cost_usd     REAL NOT NULL,
    cached       BOOLEAN NOT NULL,        -- did this hit L1/L2/L3 cache?
    timestamp    TIMESTAMP NOT NULL
);

CREATE TABLE audit_events (
    id           TEXT PRIMARY KEY,
    job_id       TEXT,
    event_type   TEXT NOT NULL,           -- 'contradiction_found' | 'page_archived' | 'cost_gate_triggered' | 'schema_failure' | etc.
    metadata     JSON NOT NULL,           -- event-specific structured data
    timestamp    TIMESTAMP NOT NULL
);
```

CLI surface: `marginalia audit history`, `marginalia audit cost --days 7`, `marginalia audit events`. Anything that exits the CLI as a table also has a `--json` flag for scripting.

This schema is enough to answer questions like: "What's our token spend per agent per week?" "Which contradictions did lint find this month?" "How often did the cache miss in the last 30 days?" Without spinning up Grafana or Datadog. SQLite + `marginalia audit` queries cover 90% of what you need.

### 13.3 The `dashboard.md` page

A live, Dataview-rendered page that lives inside the wiki at `<wiki-root>/dashboard.md`. Dataview (an Obsidian plugin) executes queries against page frontmatter and renders the results inline. The wiki *instruments itself* — no separate metrics service.

Example structure:

```markdown
# Wiki Dashboard

## Health
\`\`\`dataview
TABLE
  length(file.inlinks) as "Inbound",
  status,
  confidence
FROM ""
WHERE type = "decision" AND status = "active"
SORT file.mtime DESC
LIMIT 10
\`\`\`

## Orphans (no inbound links)
\`\`\`dataview
LIST FROM ""
WHERE length(file.inlinks) = 0 AND type != "source"
\`\`\`

## Contradictions
\`\`\`dataview
TABLE contradicts FROM "" WHERE contradicts != null
\`\`\`

## Stale (last_synced > 14 days)
\`\`\`dataview
TABLE last_synced FROM ""
WHERE date(today) - date(last_synced) > dur(14 days)
SORT last_synced ASC
\`\`\`
```

Open the wiki in Obsidian, navigate to `dashboard.md`, see live counts. Zero infrastructure. Free in both senses of the word.

### 13.4 Other metrics

- **Wiki metrics** (computed by a `metrics.yml` weekly action and committed to `lint-report.md`):
  - Pages by type, status, and confidence
  - Sources by type and authority
  - Stale page count, orphan count, contradiction count
  - Backlink density (mean inbound links per entity page)
- **Consumption metrics** (external dashboard):
  - GitHub repo views, page views by path
  - Issues filed, time-to-answer
  - PR review latency by CODEOWNER
- **Cost metrics:** per-agent per-task token spend pulled from `cost_records`, charted weekly. Validates §7.2 model selection.
- **OpenTelemetry (optional, v2).** Native OTel spans in the engine for end-to-end agent traces. Default exporter writes to `<wiki-root>/.wiki/logs/traces.jsonl`; configurable to send to Honeycomb, Tempo, Datadog, etc. via OTLP.

---

## 14. Extensibility — Hooks

Beyond the orchestrator's normal flow, the engine fires shell-script hooks on lifecycle events. Hooks are *any executable* — bash, Python, Node, Go binary — that consumes a JSON context on stdin. This makes integrating with external systems (CI, Slack notifications, dashboard refreshes, audit pipelines) trivial without coupling the engine to those systems.

### 14.1 Events

Two events at MVP, more added as needs arise:

| Event | When fired | Context shape |
|---|---|---|
| `on_ingest_complete` | After every successful ingest job, before PR opens | `{job_id, source_ref, page_paths, tokens, cost_usd, confidence}` |
| `on_lint_complete` | After every lint job | `{job_id, contradictions[], orphans[], stale[], suggested_actions[]}` |

### 14.2 Configuration

Hooks live in `<wiki-root>/.wiki/hooks/` (per-wiki) or `~/.wiki/hooks/` (global). Configured in `config.toml`:

```toml
[hooks.on_ingest_complete]
command = "~/.wiki/hooks/notify-slack.sh"
blocking = false        # fire-and-forget; ingest doesn't wait
timeout_s = 10

[hooks.on_lint_complete]
command = "~/.wiki/hooks/refresh-dashboard.py"
blocking = true         # ingest waits; non-zero exit fails the job
timeout_s = 60
```

### 14.3 Example hook (Slack notification)

```bash
#!/usr/bin/env bash
# ~/.wiki/hooks/notify-slack.sh
read -r CONTEXT
PAGES=$(echo "$CONTEXT" | jq -r '.page_paths | join(", ")')
COST=$(echo "$CONTEXT" | jq -r '.cost_usd')
curl -X POST "$SLACK_WEBHOOK" -d "{\"text\": \"Wiki updated: $PAGES (\$$COST)\"}"
```

That's the whole integration — five lines, no SDK, no auth flow inside the engine. The same shape works for "notify pager on lint contradiction," "post a tweet when a decision page is created," "kick a CI job to rebuild a downstream artifact." Same interface as git hooks; same reasons it's the right interface.

### 14.4 Why blocking matters

`blocking = true` makes the hook a *gate* — non-zero exit fails the job. Use this for hard requirements like "every ingest must pass corporate DLP scan." `blocking = false` is fire-and-forget — non-zero exit is logged but doesn't fail the job. Use this for soft signals like notifications. The default is `false` because most hooks shouldn't block the agent.

---

## 15. Roadmap

| Phase | Scope | Exit criteria |
|---|---|---|
| **MVP (4 weeks)** | Local FS adapter, Notion adapter, ingest + synthesis + QA agents (no decomposition yet), strict-schema retry, basic lint, job queue, L1+L2 cache, `purpose.md`/`AGENTS.md`/`dashboard.md` scaffold, manual ingest only | 50 sources ingested, 5 decisions documented, can answer "what did we decide about X" with citations, queue survives crash |
| **v1 (6–8 weeks)** | Slack + Hex + Granola + GDrive adapters, automated webhooks, nightly lint, CODEOWNERS routing, query decomposition + knowledge gap detection, periodic scaffold, hooks system, audit DB | All meeting notes auto-flow, dashboards as metric pages, lint catches regressions before they ship, gap loop produces measurably better answers over time |
| **v2 (12+ weeks)** | Custom search service, MCP server exposing the wiki, subscription notifications, full multi-modal, OpenTelemetry export, multi-wiki management UI | Wiki is consumable as a tool by other agents; new-hire ramp time measurably reduced; multiple wikis run side-by-side without contention |

---

## 16. Decisions & Open Questions

### 16.1 Resolved (2026-04-29)

| # | Question | Decision |
|---|---|---|
| 1 | Auto-merge for source pages | **Active.** Per §9.3 split: sources auto-merge, knowledge requires 1 reviewer, decisions require CODEOWNERS approval. |
| 2 | One repo or many? | **Single wiki repo for content.** Engine (agents, prompts, scripts, workflows) lives in a separate repo. Information flows freely within company; redaction at edge for external sharing. See §5.2.1. |
| 3 | Obsidian first-class? | **GitHub is canonical.** Obsidian remains a viewer. Schema stays Obsidian-friendly but governance does not depend on it. |
| 4 | Upstream source deletion | **Move to `<folder>/archived/`.** Status flips to `archived`, `archived_reason` and `archived_date` populated. Backlinks rewritten **synchronously in the same PR** — atomic, no broken-link window. See Scenario H. |
| 5 | Schema strictness | **Strict.** Agent retries up to 3x with validation errors in context; after 3 failures, page committed as `status: draft` with `validation_errors` populated and flagged for human review. See §7.3.1. |
| 7 | Where does `raw/` live? | **Hybrid: split by role.** `~/wiki-raw/` (local) is a transient inbox. `sources/raw/` (in repo) is a durable archive — but only for originals without an upstream home (PDFs, images, manual web clips). Files pulled via live MCPs do not get archived since they can be re-fetched from upstream. |
| 8 | Curator capture interface | **`marginalia` CLI with git-style staging.** `marginalia add` accepts files and URLs uniformly, dispatching by pattern. `marginalia ingest` commits the staged batch atomically. CLI is the stable contract; chat is a friendly skin over the same dispatcher. See §9.6. |

### 16.2 Still open

6. **Quotas per source type.** Slack threads can be high-volume and low-signal. Should the curator set ingest budgets per source to prevent the wiki from becoming a Slack mirror? *Deferred — revisit when Slack adapter is built.*

---

## Appendix: Mapping to Anthropic Architect Certification

This project is being built in part as exam preparation. The mapping makes the build deliberate.

| Exam domain | Project surface | Concrete artifact |
|---|---|---|
| API & SDK Usage (20%) | Streaming for synthesis, Batch API for nightly jobs, retries with backoff in adapters, durable job queue with retry semantics | `engine/adapters/*.py`, `engine/jobs/`, agent config |
| Prompt Engineering (20%) | Versioned prompts per agent role; XML structure; few-shot for entity extraction; CoT in synthesis and lint; strict schema with retry-on-fail; query decomposition prompts | `engine/prompts/*.md` |
| Models & Capabilities (17%) | Per-task model selection (Haiku/Sonnet/Opus), documented rationale, cost tracking, two-step ingest with model ladder | `engine/decisions/model-selection.md` |
| Architecture Patterns (17%) | Orchestrator + subagents (ingest, synthesis, QA, lint, scaffold), RAG-vs-compounding-wiki tradeoff, tool use, context management via index, three-layer cache, durable job queue, hook-based extensibility | This document, §7 |
| Tool Use (cross-cutting) | MCP source connectors + local wiki tools | `engine/tools/` |
| Safety & Constitutional AI (17%) | PII handling, ACL passthrough, refusal patterns when ingest hits sensitive content | §11; **separate study still required for theory** |
| Responsible AI & Policies (10%) | AUP-aligned filtering, secret detection | §11; **separate study required for AUP specifics** |

The two flagged domains are the parts the project alone won't teach. They need dedicated reading time in parallel with the build.
