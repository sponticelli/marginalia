---
title: Marginalia Engine
type: entity
status: active
created: 2026-04-01
last_synced: 2026-05-03
owners:
  - "[[knowledge/entities/marginalia-engine]]"
tags:
  - project
  - infrastructure
  - wiki
related:
  - "[[sources/marginalia-engine-architecture-and-model-selection]]"
  - "[[sources/marginalia-ingest-pipeline-architecture]]"
  - "[[knowledge/concepts/ingest-pipeline]]"
  - "[[knowledge/concepts/two-step-ingest-cacheability]]"
  - "[[knowledge/decisions/engine-model-ladder]]"
---

# Marginalia Engine

The LLM-maintained wiki system. The engine half (this codebase) holds
agents, adapters, prompts, the CLI, the job queue, and the cache. The
content half lives in a separate wiki repo.

Architecture is documented in
[[sources/marginalia-engine-architecture-and-model-selection]] and
[[sources/marginalia-ingest-pipeline-architecture]]; the model ladder
choice is recorded in [[knowledge/decisions/engine-model-ladder]].

Core concepts: [[knowledge/concepts/ingest-pipeline]] and
[[knowledge/concepts/two-step-ingest-cacheability]].
