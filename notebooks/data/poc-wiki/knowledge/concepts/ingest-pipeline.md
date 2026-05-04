---
title: Ingest Pipeline
type: concept
status: active
confidence: high
created: 2026-04-10
last_synced: 2026-04-28
tags:
  - ingest
  - pipeline
  - architecture
related:
  - "[[sources/marginalia-ingest-pipeline-architecture]]"
  - "[[knowledge/entities/marginalia-engine]]"
  - "[[knowledge/concepts/two-step-ingest-cacheability]]"
  - "[[knowledge/decisions/engine-model-ladder]]"
sources:
  - ref: local:ingest-pipeline-concept
    kind: local_file
    captured: 2026-04-10
    authority: canonical
---

# Ingest Pipeline

The four-stage flow that turns a raw upstream input into a wiki page:
**adapter → analyze → synthesize → upsert**.

1. **Adapter** dispatches by source type (file extension, URL pattern).
   See [[sources/marginalia-ingest-pipeline-architecture]] for the §8
   adapter contract.
2. **Analyze** (Haiku 4.5) extracts entities, classifies type, and
   summarizes — without committing. See
   [[knowledge/concepts/two-step-ingest-cacheability]] for why this
   step is split out.
3. **Synthesize** (Sonnet 4.6) produces the strict-schema page from
   the analysis artifact. Validates against `_ops/schemas/`; retries
   up to 3 times on `ValidationError`.
4. **Upsert** writes the page through `marginalia.upsert_page`.

Model choices are recorded in [[knowledge/decisions/engine-model-ladder]].
