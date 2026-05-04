---
confidence: high
contradicts: []
created: '2025-01-30'
last_synced: '2025-01-30'
owners: []
related: []
sources:
- authority: canonical
  captured: '2025-01-30'
  kind: local_file
  ref: local_file:ingest_pipeline_architecture
status: active
tags:
- ingest
- pipeline
- architecture
- adapter
- agent
- haiku
- sonnet
title: Marginalia Ingest Pipeline Architecture
type: source
validation_errors: []
---

# Marginalia Ingest Pipeline Architecture

## Overview

This source documents the four-stage linear data flow that constitutes the Marginalia ingest pipeline. The pipeline transforms a raw file dropped by a user into a structured `SourcePage` wiki page.

## Pipeline Stages

```
User drops file → Adapter (local_fs) → Ingest agent (Haiku → Sonnet) → Wiki page (SourcePage)
```

### Stage 1 — User Input

A user drops a file into the system, initiating the ingest process.

### Stage 2 — Adapter (`local_fs`)

The adapter layer handles source dispatch. For local files, the `local_fs` adapter is selected. Adapter dispatch logic is specified in design **§8**.

### Stage 3 — Ingest Agent (Haiku → Sonnet)

The ingest agent applies a two-model sequential approach:

1. **Haiku** — performs initial analysis of the source content (lightweight, fast pass).
2. **Sonnet** — performs synthesis and produces the final structured output (higher-capability pass).

The ingest agent contract and model routing policy are specified in design **§7.1**.

### Stage 4 — Wiki Page (`SourcePage`)

The pipeline produces a `SourcePage` as its output artifact, stored in the wiki.

## Key Design Decisions

- **Two-model approach**: Using Haiku for analysis and Sonnet for synthesis balances cost and quality. Haiku handles the cheaper extraction pass; Sonnet handles the more demanding synthesis pass.
- **Adapter dispatch**: The adapter layer decouples source kind detection from the ingest agent, allowing new source kinds to be added without modifying agent logic.

## References

- Design §8 — Adapter dispatch
- Design §7.1 — Ingest agent contract

## Related

- [[knowledge/entities/marginalia-engine]]
- [[knowledge/concepts/ingest-pipeline]]
- [[knowledge/concepts/two-step-ingest-cacheability]]
