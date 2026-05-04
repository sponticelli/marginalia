#!/usr/bin/env bash
# Sample non-blocking hook: pretty-print the ingest context to stdout.
# In production, replace the `cat` with a `curl -X POST $SLACK_WEBHOOK`.
#
# Contract: JSON context arrives on stdin. Exit 0 is success.

set -euo pipefail
read -r CONTEXT
echo "[notify-slack] $(echo "$CONTEXT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f"ingested {d.get(\"source_ref\",\"?\")} → {d.get(\"page_paths\",[])} (\$$d.get(\"cost_usd\",0):.4f)")')"
exit 0
