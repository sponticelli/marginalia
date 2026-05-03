---
confidence: high
contradicts: []
created: '2025-07-11'
last_synced: '2025-07-11'
owners: []
related: []
sources:
- authority: canonical
  captured: '2025-07-11'
  kind: local_file
  ref: local:3b4decacb6a8d9b6e14ec193c11be01582ec67ab08ce045f63d0054debd6416d
status: active
tags:
- architecture
- pipeline
- model-selection
- cache-discipline
- ingest
- synthesis
title: Marginalia Engine Architecture and Model Selection
type: source
validation_errors: []
---

# Marginalia Engine Architecture and Model Selection

## Overview

The Marginalia engine operates as a two-stage pipeline: **analyze** and **synthesize**. Together these stages transform raw upstream sources into validated, schema-conformant wiki pages.

---

## Pipeline Stages

### Stage 1 — Analyze

- Processes a **single source** at a time.
- Uses **deterministic prompts** to extract structured features (entities, tags, summary, confidence, etc.).
- Outputs an analysis artifact consumed by the synthesize stage.

### Stage 2 — Synthesize

- Composes strict-schema `SourcePage` output from the analysis artifact.
- Includes **validation retry logic**: if the produced page fails schema validation, the stage retries with error context injected into the prompt.

---

## Source Adapter Dispatch

Adapters are selected by **file extension**:

| Extension / Type | Adapter Behavior |
|---|---|
| `.md` | Markdown parser |
| `.txt` | Plain-text reader |
| `.pdf` | PDF extractor; falls back to **vision** if text extraction fails |
| Image formats | Vision model path |

---

## Model Selection Strategy

Model assignment is **task-specific**, not one-size-fits-all:

| Model | Assigned Tasks |
|---|---|
| **Haiku 4.5** | High-volume summarization, entity extraction |
| **Sonnet 4.6** | Cross-page synthesis, Q&A with citations |
| **Opus 4.7** | Contradiction detection, lint passes |

All models are provided by **Anthropic**.

---

## Cache Discipline

Cache keys are composed of three components:

```
SHA-256(content) + prompt_version + CACHE_VERSION
```

- **`CACHE_VERSION`** is a constant that must be bumped whenever prompt logic changes in a way that would alter outputs.
- **Risk**: bumping the prompt version without also incrementing `CACHE_VERSION` (or vice versa) can cause the engine to serve **stale cached responses** against updated logic.

> Operational note: any change to prompt templates should be accompanied by a `CACHE_VERSION` increment to guarantee cache invalidation.

---

## Key Entities

- `Marginalia engine` — the ingest and synthesis system documented here.
- `CACHE_VERSION` — constant controlling cache invalidation scope.
- `Haiku 4.5`, `Sonnet 4.6`, `Opus 4.7` — Anthropic models used at different pipeline tiers.
