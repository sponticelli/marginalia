# Getting started with Marginalia

A step-by-step guide for first-time users. By the end you will have:

1. The `marginalia` CLI installed and working.
2. A *content wiki* — your own folder of markdown pages — registered with the engine.
3. Your first source ingested into that wiki, end to end.
4. Enough vocabulary to read the rest of the docs without getting lost.

You do not need to read the engine code. You do need a terminal, an
Anthropic API key, and roughly 20 minutes the first time.

---

## 0 · How Marginalia is laid out

Two folders matter, and they are **not the same folder**:

| Folder | What lives there | Who creates it |
|---|---|---|
| **Engine repo** (this one) | Python code, CLI, agents, prompts, job queue | You cloned it |
| **Content wiki** | Your actual markdown pages (`entities/`, `knowledge/`, `sources/`, `purpose.md`, `AGENTS.md`) | You create it once, see §3 |
| **Inbox** (`~/wiki-raw` by default) | Files and URL markers waiting to be ingested | Created lazily when you stage your first source |

If you forget which folder a command writes to, the rule is:

- `marginalia add` writes into the **inbox**.
- Almost everything else (`ingest`, `ask`, `search`, `scaffold`, `lint`,
  `log`) reads/writes the **content wiki** at `$WIKI_CONTENT_REPO`.
- Code changes go in this engine repo and only this engine repo.

---

## 1 · Prerequisites

| Requirement | Why | How to check |
|---|---|---|
| Python 3.12+ | Engine targets py312 | `python3 --version` |
| `uv` package manager | Project uses `uv.lock` | `uv --version` — install from <https://docs.astral.sh/uv/> if missing |
| `git` | Wiki content repo is git-versioned | `git --version` |
| Anthropic API key | Every agent call uses Claude | Get one at <https://console.anthropic.com> |
| (Optional) `gh` CLI | Needed if you want `marginalia` to open GitHub PRs | `gh --version` — install from <https://cli.github.com> |

You do **not** need Docker, Postgres, or any cloud account. The job
queue and audit log are local SQLite files.

---

## 2 · Install the engine

```bash
# 1. Clone (skip if you already have it)
git clone https://github.com/<your-fork-or-the-canonical>/marginalia.git
cd marginalia

# 2. Install dependencies into a project-local virtualenv (.venv/)
uv sync --all-extras

# 3. Smoke test
uv run marginalia hello sandro
# → Hello, sandro! Marginalia engine is alive.

uv run marginalia version
# → engine v0.1.0
```

`uv sync --all-extras` pulls the `dev`, `notebooks`, and `scheduler`
extras as well as the runtime deps. If you only want the runtime,
`uv sync` is enough — but the rest of this guide assumes the extras
are installed.

> **Tip — drop the `uv run` prefix.** Activate the venv once with
> `source .venv/bin/activate` and you can call `marginalia` directly
> for the rest of your shell session. The examples below keep `uv run`
> for clarity.

---

## 3 · Create your first content wiki

The engine needs a folder of markdown to write into. Create one now —
it is a normal directory, optionally a git repo, with two seed files
the agents read at runtime.

```bash
# Pick a location. ~/wikis/personal is fine; anywhere works.
mkdir -p ~/wikis/personal
cd ~/wikis/personal
git init -q

# Two required seed files. Both are short — fill them in below.
touch purpose.md AGENTS.md
mkdir -p entities knowledge sources

cd -    # back to the engine repo
```

**`purpose.md`** — one paragraph telling the agents what this wiki is
*for*. Used to filter what gets ingested. Example:

```markdown
# Purpose

A personal research wiki on **distributed systems and database
internals**. In scope: papers, talks, blog posts, book chapters on
storage engines, consensus, replication, query planning. Out of scope:
generic web dev, framework tutorials, news.
```

**`AGENTS.md`** — terminology, naming, and style rules the agents
follow when writing pages. Minimal version:

```markdown
# Agents

## Style
- One concept per page. Short titles. Prefer concrete examples.
- Cite every claim with a wikilink to its source page.

## Terminology
- Prefer "log-structured merge tree" over "LSM" on first mention.
- Prefer "consensus" over "paxos" as the umbrella term.
```

You can leave both files almost empty for now — `marginalia scaffold`
(see §9) will help you fill them in based on what you actually ingest.

---

## 4 · Configure the engine

### 4.1 — Environment variables

Copy the template, then edit:

```bash
# (still in the engine repo)
cp .env.example .env
```

Edit `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-…           # required
WIKI_RAW_PATH=~/wiki-raw             # optional; default is ~/wiki-raw
WIKI_CONTENT_REPO=~/wikis/personal   # the folder you made in §3
```

> **The CLI does *not* auto-load `.env`.** You have to export the
> values into your shell yourself. The simplest one-liner:
>
> ```bash
> set -a; source .env; set +a
> ```
>
> Run it once per shell session, or put it in your shell rc, or use
> [`direnv`](https://direnv.net/) so it happens automatically when you
> `cd` into the repo. The `.env.example` filename is a convention for
> the file's *contents*; it is not loaded magically.

### 4.2 — Register the wiki with the multi-wiki registry

Marginalia supports multiple wikis (work, personal, a book club). The
*active* wiki is what every command targets unless you pass
`--wiki-root`. Register the one you just made and mark it active:

```bash
uv run marginalia wikis add personal ~/wikis/personal
# → registered personal → /Users/you/wikis/personal  (now active)

uv run marginalia wikis list
# Pretty table; the active wiki has a ★ next to it.
```

Behind the scenes this writes `~/.config/marginalia/wikis.toml`. From
now on commands like `marginalia ask "..."` will target
`~/wikis/personal` automatically.

To switch wikis later: `marginalia use <name>`.

### 4.3 — (Optional) GitHub PR integration

If you want `marginalia ingest --wait` to open a pull request after a
successful page write, log in once with the `gh` CLI:

```bash
gh auth login
```

The wiki must be a git repo with a remote. If you skip this, pass
`--no-pr` whenever you ingest, and the CLI will commit locally only.

---

## 5 · Your first ingest

Pick something small — a single article URL, a PDF, or a markdown
file. We will use a YouTube talk because the YouTube adapter needs no
API keys.

```bash
# 1. Stage the source. Cheap; just records the URL in the inbox.
uv run marginalia add "https://www.youtube.com/watch?v=EXAMPLE_ID"
# → staged URL → www-youtube-com-1a2b3c4d.url

# 2. See what's staged.
uv run marginalia status
# Shows a table: name, kind=url, source=full URL.

# 3. Ingest — and wait so you actually see the result.
uv run marginalia ingest --wait
# Streams worker progress. On success, prints the new page path
# inside ~/wikis/personal/sources/youtube/<slug>.md and (if gh is
# configured) a PR URL.
```

What just happened:

1. `add` wrote a `.url` marker file into `~/wiki-raw/`. No fetch yet.
2. `ingest` enqueued an `ingest` job into `~/wikis/personal/.wiki/jobs.db`.
3. `--wait` spun up a one-shot worker that ran the job to completion.
4. The worker called the **analyze** step (Haiku 4.5, cacheable), then
   the **synthesize** step (Sonnet 4.6), validated the result against
   the page schema, and committed a new file to the wiki.

Open `~/wikis/personal/sources/youtube/` in your editor — the new page
is there, with YAML frontmatter and a body of cited claims.

> **No `--wait`?** The job stays queued. You can drain it later with
> `uv run marginalia worker --drain` or watch progress with
> `marginalia jobs list`. `--wait` is the beginner-friendly default.

### Other things you can stage

```bash
# A local PDF
uv run marginalia add ~/Downloads/some-paper.pdf

# A markdown clipping
uv run marginalia add ~/notes/raw-clipping.md

# Preview the analyze step without committing anything
uv run marginalia add "https://example.com/article" --analyse-only
```

`--analyse-only` is the fastest way to learn what the agent thinks
about a source before you commit to ingesting it. It prints proposed
title, type, entities, and tags, then exits.

> **URL coverage.** `add <url>` and the full `ingest` path route URLs
> by host to a dispatcher: YouTube, Google Docs, Gmail, Notion,
> GitHub, Apple Notes (`notes://` URL scheme), and a generic `web`
> adapter (trafilatura) for anything else. Unknown extensions on local files
> raise `UnsupportedExtensionError` rather than silently passing
> through, so adapter gaps are loud.
>
> One caveat for `add --analyse-only`: the *preview* path currently
> labels every URL as `youtube_video` regardless of host, so the
> proposed title/type/tags will be miscalibrated for non-YouTube
> URLs. The full `ingest` path uses the correct adapter — the limit
> is only in the preview.

---

## 6 · Ask the wiki questions

Once you have a few pages ingested:

```bash
uv run marginalia ask "What does the wiki say about LSM compaction?"
```

The orchestrator decomposes the question, retrieves matching pages,
and answers with citations. Cost and wall-time are printed underneath.

Persist the question and its answer as a permanent QA page:

```bash
uv run marginalia ask "..." --save
# → saved → ~/wikis/personal/sources/qa/<slug>.md
```

Saved QA pages are first-class wiki citizens — they show up in search,
in the index, and in the dashboard. This is what makes Marginalia
"compounding" rather than transient chat.

### Plain text search (no LLM call, no API cost)

```bash
uv run marginalia search "consensus"
uv run marginalia search "raft" --type entity --limit 5
```

Useful for "is this concept already in the wiki?" before ingesting a
new source.

---

## 7 · Day-to-day verbs

| Command | What it does | When you'd use it |
|---|---|---|
| `marginalia add <ref>` | Stage a file or URL into the inbox | Saw a paper / talk you want in the wiki |
| `marginalia status` | List inbox contents | "What's queued for ingest?" |
| `marginalia unstage <ref>` | Drop an item from the inbox | Changed your mind |
| `marginalia ingest [--wait]` | Run ingest jobs for everything in the inbox | Process the queue |
| `marginalia ask "<q>" [--save]` | Ask the wiki a question | Anytime you'd otherwise grep |
| `marginalia search "<terms>"` | Term-overlap search, no LLM | "Do I already have a page on X?" |
| `marginalia resync <page>` | Re-run the original adapter for a page | Source updated; refresh the page |
| `marginalia log -n 20` | Show recent ingests from the audit DB | "What did I ingest this week?" |
| `marginalia lint [--scope stale\|orphans\|contradictions]` | Find stale, orphaned, or contradictory pages | Periodic hygiene |
| `marginalia scaffold --target index\|purpose\|agents\|dashboard` | Regenerate a meta-page | After a few dozen pages exist |
| `marginalia use <name>` | Switch active wiki | Jumping between work/personal |

Each command takes `--help` for full flag detail.

---

## 8 · The job queue (when things take longer)

If you ingest 30 URLs at once, you do not want to block your terminal.
The pattern:

```bash
# Stage everything.
for url in $(cat ~/reading-list.txt); do
  uv run marginalia add "$url"
done

# Enqueue without waiting.
uv run marginalia ingest

# Run a worker in the background. Ctrl-C stops it cleanly.
uv run marginalia worker
```

Useful inspection commands:

```bash
uv run marginalia jobs list                      # all jobs and their state
uv run marginalia jobs status <id>               # detail for one job
uv run marginalia jobs retry <id>                # re-run a failed job
uv run marginalia jobs cancel                    # cancel all pending jobs
uv run marginalia jobs purge --older-than 30     # delete done/dead rows >30 days old
```

Failed jobs preserve their error and do not silently drop. The queue
is durable: kill the worker, restart it, jobs resume.

---

## 9 · Maintenance

These do not need to run every day, but they keep the wiki healthy.

### `scaffold` — regenerate meta-pages

```bash
# index.md is fully derived from page state. Always written.
uv run marginalia scaffold --target index

# purpose.md / AGENTS.md are human-authored. Default writes a
# .proposed file for you to diff and review.
uv run marginalia scaffold --target purpose
diff ~/wikis/personal/purpose.md ~/wikis/personal/purpose.md.proposed
uv run marginalia scaffold --target purpose --apply  # accept

# dashboard.md is deterministic — no LLM call, always overwrites.
uv run marginalia scaffold --target dashboard
```

### `lint` — surface drift

```bash
uv run marginalia lint                       # full pass; writes lint-report.md
uv run marginalia lint --scope stale         # cheap, no LLM call
uv run marginalia lint --scope orphans       # cheap, no LLM call
uv run marginalia lint --scope contradictions  # uses Opus
```

### `cache` — inspect and groom the LLM cache

```bash
uv run marginalia cache stats   # hit rates, size on disk
uv run marginalia cache gc      # remove expired entries
uv run marginalia cache clear   # nuke everything (rarely needed)
```

The cache is what makes prompt iteration affordable: re-ingesting a
source after a prompt edit only re-runs the cheap synthesize step. If
you see unexpectedly old behavior after editing a prompt, the prompt
author forgot to bump `CACHE_VERSION` — flag it, do not paper over
with `cache clear`.

### `audit` — what did the engine do, and what did it cost

```bash
uv run marginalia audit history     # ingests over time
uv run marginalia audit cost        # spend by model / by day
uv run marginalia audit events      # all worker events
```

---

## 10 · Common first-day problems

| Symptom | Cause | Fix |
|---|---|---|
| `marginalia: command not found` | Forgot `uv run`, or `.venv` not active | `uv run marginalia ...` or `source .venv/bin/activate` |
| `WikiConfigError: missing required wiki-config file(s): purpose.md, AGENTS.md` | Skipped §3, or active wiki points at a directory without those seed files | Create them (see §3) or fix the registry with `marginalia wikis add`/`marginalia use` |
| `AuthenticationError` / `ANTHROPIC_API_KEY not set` | `.env` is on disk but never loaded into the shell | `set -a; source .env; set +a` (see §4.1) |
| `pr_create` job fails | `gh` not authed, or wiki has no remote | `gh auth login`, or pass `--no-pr` to `ingest` |
| Ingest job stays "pending" forever | No worker running, and you skipped `--wait` | `marginalia worker --drain` |
| Page lands with `status: draft` and `validation_errors` filled in | Schema retry exhausted (3 attempts) | Read the errors, fix the source, `marginalia resync <page>` |
| YouTube transcript fetch fails | Video has no transcript / region-locked | Try a different video; the adapter does not synthesize fake captions |

---

## 11 · Where to go next

- **`docs/marginalia-design.md`** — the full architecture spec. The
  source of truth when CLI help and reality disagree.
- **`docs/notebook-plan.md`** — the 11-notebook implementation
  sequence. If you want to *understand* (not just *use*) the engine,
  read this and run the notebooks in order.
- **`README.md`** — the elevator pitch and "why this exists."
- **`CLAUDE.md`** — repo conventions; required reading before you open
  a PR against the engine.

The engine is early. CLI verbs may grow new flags; some adapters are
still wiring up. When help text and this guide disagree, trust
`marginalia <verb> --help`.
