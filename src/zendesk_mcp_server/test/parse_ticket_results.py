import json
import sys
from pathlib import Path


def load_payload(raw: object) -> dict:
    if isinstance(raw, dict):
        if "structuredContent" in raw and isinstance(raw["structuredContent"], dict):
            return raw["structuredContent"]

        if "result" in raw and isinstance(raw["result"], dict):
            return load_payload(raw["result"])

        return raw

    if isinstance(raw, list):
        if not raw:
            return {}

        first = raw[0]
        if isinstance(first, dict) and "text" in first:
            return json.loads(first["text"])

        if isinstance(first, dict) and first.get("type") == "resource":
            resource = first.get("resource", {})
            if isinstance(resource, dict) and "text" in resource:
                return json.loads(resource["text"])

    raise ValueError("Unsupported tool result format")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python parse_ticket_results.py <tool-result.json>", file=sys.stderr)
        return 1

    path = Path(sys.argv[1])
    raw = json.loads(path.read_text())
    payload = load_payload(raw)
    tickets = payload.get("tickets", [])

    print(f"Total tickets: {len(tickets)}")
    for ticket in tickets:
        print(
            f'ID: {ticket.get("id")} | '
            f'Created: {ticket.get("created_at")} | '
            f'Subject: {ticket.get("subject")}'
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
