# Marginalia

**An LLM-maintained wiki that compounds knowledge instead of retrieving it.**

*Notes between the lines.*

---

## What it is

Marginalia reads your sources — PDFs, articles, Notion pages, Slack threads, meeting transcripts, YouTube videos — and compiles them into a structured, cross-linked wiki of markdown pages. Cross-references are built automatically. Contradictions are surfaced, not blended. Every claim cites its source. The wiki keeps itself current as your content evolves.

The output is a folder of plain markdown files with YAML frontmatter, browseable in any editor, version-controlled in git, viewable in Obsidian. No proprietary format. No lock-in. Close the tool — the knowledge stays.

## Why it exists

Most knowledge tools retrieve and summarize at query time. RAG chunks documents and stitches fragments together when you ask a question. The work happens once, then is thrown away. Ask the same question tomorrow and the LLM rediscovers everything from scratch.

Marginalia inverts this. It **compiles knowledge at ingest time.** Every new source enriches and links the entire corpus, not just appends a chunk waiting to be retrieved. The wiki itself is the artifact — synthesized once, kept current, queryable forever after.

The historical analogue is the marginalia tradition: scholars annotated books in the margins, those notes became cross-references, and the cross-references became their own knowledge layer that other scholars built on. That's what this project does — at machine speed, against your sources, for your purposes.

## What it's good for

- **Personal research.** Months of articles, papers, and podcast notes turned into a structured, navigable wiki on the topic you've been digging into.
- **Reading a book or a course.** A companion wiki that grows chapter by chapter, with pages for characters, themes, and concepts cross-linked as they appear.
- **Team knowledge.** Decisions, meetings, projects, and customer conversations captured into a shared, searchable record that doesn't depend on anyone remembering where the Slack thread was.
- **Due diligence and competitive analysis.** A persistent dossier that compounds with every new source instead of starting from a blank doc each time.
- **Onboarding.** A new hire reading the wiki in week one, getting context that would otherwise require ten 1:1s.

If you're accumulating knowledge over time and want it organized rather than scattered, Marginalia is the shape of the answer.

## How it differs from the alternatives

| Capability | Marginalia | RAG | NotebookLM | Notion AI |
|---|---|---|---|---|
| Synthesis at ingest, not query | Yes | No | Partial | No |
| Contradictions surfaced, not blended | Yes | No | No | No |
| Persistent cross-reference graph | Yes | No | No | No |
| Plain markdown, no proprietary format | Yes | Varies | No | No |
| Local-first; sources never leave your machine | Yes | Varies | No | No |
| Browsable without the tool running | Yes | No | No | No |

The differentiator is the artifact. RAG produces an answer; Marginalia produces a wiki. Answers are disposable. The wiki compounds.

## Design principles

- **The artifact outlives the tool.** Markdown files in a git repo. Open in any editor.
- **Provenance is first-class.** Every claim cites a source. Every source page records its origin, capture time, and authority.
- **Contradictions are evidence, not bugs.** When two sources disagree, both are preserved with the disagreement annotated — not silently averaged.
- **Humans curate, the LLM maintains.** You direct what comes in and what gets asked. The agent does the bookkeeping no human ever wants to do.
- **The wiki tells you what it doesn't know.** Thin queries don't return weak answers; they return suggestions for what to ingest next.

## What's inside

- A **CLI** (`wiki`) with git-style staging — `wiki add`, `wiki status`, `wiki ingest`, `wiki ask`.
- A **multi-agent engine** built on Claude — orchestrator, ingest, synthesis, QA, lint, and scaffold agents, each tuned for its task.
- **Source adapters** for local files, Notion, Slack, Hex, Granola, Google Drive, YouTube, and the web.
- A **durable job queue** so batch ingests survive crashes.
- A **strict-schema page model** — every page has typed frontmatter, validated on every write.
- **Hooks** for integrating with anything else you care about — CI, notifications, dashboards.
- **Lives inside Obsidian** if you want a graph view and live dashboards.

Installation, configuration, and a step-by-step quick-start are in [docs/](docs/). The full design is in [docs/design.md](docs/design.md).

## Status

Early. Built in the open. The design is settled enough to scaffold against; the implementation is being built notebook by notebook (see [docs/notebook-plan.md](docs/notebook-plan.md)).

## Inspiration

> *"The LLM should be able to maintain a wiki for you."*
> — Andrej Karpathy, [LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

Karpathy sketched the personal version. Marginalia takes the same pattern further — adds durability, adds adapters for the systems where company knowledge actually lives, adds the tools that make this useful past a single laptop. The vision is older than that, though. Vannevar Bush described the Memex in 1945 — a personal store of curated knowledge with associative trails between documents. The part Bush couldn't solve was who does the maintenance. The LLM handles that.

## License

TBD.
