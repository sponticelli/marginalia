#!/usr/bin/env python3
"""Sample blocking hook: reject low-confidence ingests of sensitive sources.

Reads JSON context on stdin. The context shape produced by
``_handle_ingest`` includes ``source_ref``, ``page_paths``,
``confidence``, ``cost_usd``. Exits 1 (failure) when the source path
contains "customer-data" AND the confidence is "low" — the typical
shape of a real DLP gate.

Otherwise exits 0 to let the ingest commit.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    raw = sys.stdin.read()
    try:
        ctx = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"gate-on-confidence: bad context JSON: {exc}", file=sys.stderr)
        return 2

    source = str(ctx.get("source_ref", ""))
    confidence = str(ctx.get("confidence", "")).lower()

    if "customer-data" in source and confidence == "low":
        print(
            f"gate-on-confidence: REJECTED — low-confidence ingest of {source!r}",
            file=sys.stderr,
        )
        return 1

    print(f"gate-on-confidence: OK — {source!r} @ {confidence!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
