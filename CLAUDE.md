# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

The **engine** half of Marginalia — an LLM-maintained wiki system. The engine (this repo) holds agents, adapters, prompts, the CLI, the job queue, and the cache. The **content** lives in a separate wiki repo (markdown pages, frontmatter, `_ops/schemas/`); see `docs/marginalia-design.md` §5.2.1 for the two-repo split rationale.

The repo is **early**. Most of `engine/` currently consists of empty package `__init__.py` files. The substantive design is in `docs/`:

- `docs/marginalia-design.md` — full architecture spec (the source of truth).
- `docs/notebook-plan.md` — the 11-notebook implementation sequence.

When in doubt about intent, read the design doc before changing code.

## Development workflow — notebook-first

Work is built notebook-by-notebook in `notebooks/` (not yet committed; see `notebook-plan.md`). Each notebook proves a slice of the architecture end-to-end, then the **stabilized** Pydantic models, prompts, and agent code are *extracted* into `engine/`. Notebooks are demonstration-only; outputs are stripped by `nbstripout` on pre-commit and never source-of-truth.

**Implication for Claude:** prefer extending an existing notebook to writing new files under `engine/` when prototyping a new capability. Only land code in `engine/` once it has been demonstrated and the contract is settled.

## Common commands

This project uses **uv** for dependency management (`uv.lock` is committed; `pyproject.toml` declares deps).

```bash
# install (creates .venv and installs all extras for dev work)
uv sync --all-extras

# run the CLI (installed as a script via [project.scripts])
uv run marginalia --help
uv run marginalia hello sandro      # smoke test
uv run marginalia version

# tests
uv run pytest                       # all tests; verbose, short tracebacks
uv run pytest tests/test_smoke.py::test_cli_version    # single test

# lint + format (ruff is the only linter/formatter)
uv run ruff check .
uv run ruff check . --fix
uv run ruff format .

# pre-commit (ruff, ruff-format, nbstripout, file hygiene)
uv run pre-commit run --all-files
uv run pre-commit install           # one-time, to enable the hook
```

`pytest` runs with `asyncio_mode = "auto"` — async test functions don't need an explicit `@pytest.mark.asyncio` decorator.

## Required environment

`.env` (see `.env.example`):

- `ANTHROPIC_API_KEY` — required for any agent code or notebook that calls the API.
- `WIKI_RAW_PATH` — local inbox for source files staged via `marginalia add`. Defaults to `~/wiki-raw`.
- `WIKI_CONTENT_REPO` — path to the *separate* content wiki repo the engine writes into.

## Architecture — orientation

The engine implements an **orchestrator + subagents** pattern over a **durable job queue** with a **three-layer cache**. Read `docs/marginalia-design.md` §7 for the full picture; the highlights:

```
engine/
├── cli/         # Typer app; entry point at engine.cli.main:app
├── agents/
│   ├── orchestrator/   # routes work, composes PR descriptions (Sonnet 4.6)
│   ├── ingest/         # two-step: analyze (Haiku 4.5) → synthesize (Sonnet 4.6)
│   ├── synthesis/      # cross-page reasoning, patch sets (Sonnet 4.6)
│   ├── qa/             # decomposition + retrieval + gap detection (Sonnet 4.6, Opus 4.7 escalation)
│   ├── lint/           # nightly contradiction/staleness scan (Opus 4.7)
│   └── scaffold/       # regenerates index.md/purpose.md/AGENTS.md (Sonnet 4.6)
├── adapters/    # one per source type — local_fs, youtube, notion, slack, hex, granola, gdrive, web
│                # (_template/ is the contract reference for new adapters)
├── tools/       # marginalia.search, .read_page, .upsert_page, .find_related, .open_pr, .lint_check
├── prompts/     # versioned per agent role; bump CACHE_VERSION whenever you edit one (see below)
├── models/      # Pydantic page schemas + wiki_config (purpose.md/AGENTS.md loader)
├── jobs/        # SQLite job queue; durable, resumable, retryable
├── cache/       # L1 (analyze step) + L2 (any deterministic LLM call); L3 is Anthropic's prompt cache
├── audit/       # SQLite audit DB: ingest_history, cost_records, audit_events
├── hooks/       # on_ingest_complete / on_lint_complete shell-out integration
└── utils/       # cost_tracker, etc.
```

### Three load-bearing patterns

1. **Two-step ingest with model ladder.** Ingest is split into `analyze` (cacheable, Haiku 4.5) and `synthesize` (state-dependent, Sonnet 4.6). The split is what makes prompt iteration affordable — re-ingesting only re-runs the cheap second step. Don't collapse them.

2. **Strict-schema retry, never silent failure.** Every page write validates against a JSON schema in the wiki repo's `_ops/schemas/`. On `ValidationError`, errors are fed back as `<validation_errors>` and the agent retries (max 3). After 3 failures the page lands as `status: draft` with `validation_errors[]` populated — never a silent commit. See design §7.3.1.

3. **`CACHE_VERSION` discipline.** L1/L2 cache keys include a `CACHE_VERSION` constant. **Every prompt edit must ship with a `CACHE_VERSION` bump in the same PR**, otherwise re-running ingest serves stale responses against the new prompt — an invisible bug. Treat it like a lockfile.

### What lives in the *content* repo (not here)

- The wiki itself (`entities/`, `knowledge/`, `sources/`).
- `purpose.md` (scope) and `AGENTS.md` (style/terminology) — wiki-level config the agents read at runtime.
- `_ops/schemas/*.json` — JSON schemas for frontmatter validation. The Pydantic models in `engine/models/` are the source; the JSON schemas are exported for the wiki repo to consume.
- `dashboard.md` — Dataview-rendered live health view (Obsidian).

The engine writes into the content repo via git through `marginalia.upsert_page` / `marginalia.open_pr`. No wiki content should ever be checked into *this* repo.

## Repo conventions

- **Python 3.12+ required** (`requires-python = ">=3.12"`). The codebase targets `py312` for ruff.
- **Ruff** is the only linter/formatter. Selected rules: `E, F, I, B, UP, SIM`. Line length 100 (long-line warnings ignored — formatter handles wrapping).
- `notebooks/**` is excluded from ruff.
- **Large files are blocked** by pre-commit at 500 KB. PDFs/images/video/audio go through git-lfs (`.gitattributes` already routes them).
- `notebooks/data/poc-wiki/**` is **tracked** — canonical fixtures for the notebook demos. Don't gitignore. The layout mirrors what would be two separate locations in production:
  - `poc-wiki/raw/` — the **inbox** (production: `~/wiki-raw/` per `WIKI_RAW_PATH`). Unprocessed inputs to ingest: markdown clips, PDFs, images. NB 02/04 read from here.
  - `poc-wiki/sources/` — **wiki content** (production: the wiki repo's `sources/` directory). Processed `SourcePage` files (frontmatter + body) — outputs of ingest, inputs to cross-source synthesis. NB 02/04 produce these (via `notebooks/_ops/build_source_pages.py` for the committed canonical set); NB 05+ read from here.
  - `poc-wiki/{purpose.md,AGENTS.md}` — wiki-level config the agents read at runtime (loaded by `engine.models.wiki_config.MarginaliaConfig`).

  **Don't conflate `~/wiki-raw/` (inbox) with `sources/raw/` (in-wiki durable archive of originals).** Design.md uses both; they're different things. The PoC only models the inbox role under `poc-wiki/raw/`.
- `.env` is gitignored; never commit secrets.

## CLI surface (target — most not yet implemented)

The full CLI design is in design.md §9.6. Implemented today: `marginalia hello`, `marginalia version`. The intended verbs (capture, ingest, ask, jobs, cache, audit, scaffold) are documented but unbuilt — when adding a new command, add it to `engine/cli/main.py` as a Typer command and mirror the design's flag conventions.
