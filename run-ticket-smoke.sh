#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -x ".venv/bin/python" ]; then
  echo "Missing .venv/bin/python. Run 'uv sync' first." >&2
  exit 1
fi

exec .venv/bin/python src/zendesk_mcp_server/test/smoke_get_tickets.py "$@"
