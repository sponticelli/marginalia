---
title: Two-Step Ingest Cacheability
type: concept
status: active
confidence: high
created: 2026-04-10
last_synced: 2026-04-28
tags:
  - ingest
  - cache
  - cost
related:
  - "[[sources/marginalia-engine-architecture-and-model-selection]]"
  - "[[sources/marginalia-ingest-pipeline-architecture]]"
  - "[[knowledge/concepts/ingest-pipeline]]"
  - "[[knowledge/decisions/engine-model-ladder]]"
sources:
  - ref: local:two-step-cacheability-concept
    kind: local_file
    captured: 2026-04-10
    authority: canonical
---

# Two-Step Ingest Cacheability

The split between **analyze** and **synthesize** is what makes prompt
iteration affordable. Analyze is a pure function over the source
content + prompt version: same input → same output. It's cacheable.
Synthesize depends on the live state of the wiki and isn't worth
caching the same way.

Cache key for analyze:

```
SHA-256(content) + prompt_version + CACHE_VERSION
```

Re-ingesting only re-runs the cheap second step under typical prompt
iteration. See [[sources/marginalia-engine-architecture-and-model-selection]]
for the cache discipline rationale, and
[[knowledge/concepts/ingest-pipeline]] for where this fits in the
larger flow.
