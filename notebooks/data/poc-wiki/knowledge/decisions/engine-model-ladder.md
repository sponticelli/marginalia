---
title: Engine Model Ladder
type: decision
status: active
confidence: high
created: 2026-04-05
last_synced: 2026-04-25
owners:
  - "[[knowledge/entities/marginalia-engine]]"
tags:
  - architecture
  - models
  - cost
related:
  - "[[sources/marginalia-engine-architecture-and-model-selection]]"
  - "[[knowledge/entities/marginalia-engine]]"
  - "[[knowledge/concepts/two-step-ingest-cacheability]]"
sources:
  - ref: local:engine-model-ladder-decision
    kind: local_file
    captured: 2026-04-05
    authority: canonical
---

# Engine Model Ladder

The engine assigns models per task tier:

- **Haiku 4.5** — high-volume, cacheable analyze step (ingest stage 1).
- **Sonnet 4.6** — synthesis, cross-page reasoning, QA with citations.
- **Opus 4.7** — contradiction detection (lint), gap escalation, deep
  reasoning passes.

Rationale and the cost-per-tier receipts live in
[[sources/marginalia-engine-architecture-and-model-selection]]. The
ladder is the cost-engineering counterpart to
[[knowledge/concepts/two-step-ingest-cacheability]] — the analyze step
is cheap precisely so re-running it under prompt iteration is
affordable.
