#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -x ".venv/bin/python" ]; then
  echo "Missing .venv/bin/python. Run 'uv sync' first." >&2
  exit 1
fi

.venv/bin/python -m unittest src/zendesk_mcp_server/test/ticket_test.py "$@"

echo
echo "Verification summary:"
echo "- Client last_hours=5 query test passed"
echo "- MCP get_tickets structured output test passed"
echo "- Mocked ticket count in fixture: 1"
