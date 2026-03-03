import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from zendesk_mcp_server.ticket_field_metadata import TicketFieldOptionResolver
from zendesk_mcp_server.zendesk_client import ZendeskClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect Zendesk ticket field metadata and loaded option maps."
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all loaded option values instead of truncating to 10 per field.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of option mappings to show per field unless --show-all is used.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    load_dotenv()

    subdomain = os.getenv("ZENDESK_SUBDOMAIN")
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_KEY")

    missing = [
        name
        for name, value in (
            ("ZENDESK_SUBDOMAIN", subdomain),
            ("ZENDESK_EMAIL", email),
            ("ZENDESK_API_KEY", token),
        )
        if not value
    ]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1

    client = ZendeskClient(subdomain=subdomain, email=email, token=token)
    resolver = TicketFieldOptionResolver(client)
    resolver.load()

    if not resolver.option_maps:
        print("No relevant ticket field option maps were loaded.")
        return 0

    print("Loaded ticket field option maps:")
    for field_name, option_map in sorted(resolver.option_maps.items()):
        print(f"- {field_name}: {len(option_map)} options")
        items = sorted(option_map.items())
        if not args.show_all:
            items = items[: args.limit]
        for raw_value, display_value in items:
            print(f"  {raw_value} -> {display_value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
