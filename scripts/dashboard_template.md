# Wiki Dashboard

_Static template — copy to `<wiki>/dashboard.md` and let Obsidian's Dataview plugin render the live data._

## Health (active decisions)

```dataview
TABLE
  length(file.inlinks) as "Inbound",
  status,
  confidence
FROM ""
WHERE type = "decision" AND status = "active"
SORT file.mtime DESC
LIMIT 10
```

## Orphans (no inbound links)

```dataview
LIST FROM ""
WHERE length(file.inlinks) = 0 AND type != "source"
```

## Contradictions

```dataview
TABLE contradicts
FROM ""
WHERE contradicts != null
```

## Stale (last_synced > 14 days)

```dataview
TABLE last_synced
FROM ""
WHERE date(today) - date(last_synced) > dur(14 days)
SORT last_synced ASC
```

## Cost — last 7 days

_This section is server-rendered by `engine.audit.dashboard.generate_dashboard()`. Run it after a busy day to refresh._

| Day | Calls | Cost (USD) |
|---|---|---|
| _(empty)_ | — | — |
