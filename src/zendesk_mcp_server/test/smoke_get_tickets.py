import argparse
import os
import sys

from dotenv import load_dotenv

from zendesk_mcp_server.zendesk_client import ZendeskClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Live smoke check for Zendesk tickets updated in the last N hours."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=5,
        help="Look back this many hours. Default: 5",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=25,
        help="Max tickets to request in this smoke run. Default: 25",
    )
    parser.add_argument(
        "--show",
        type=int,
        default=10,
        help="How many ticket rows to print. Default: 10",
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
    result = client.get_tickets(last_hours=args.hours, per_page=args.per_page)
    tickets = result.get("tickets", [])

    print("Smoke result:")
    print(f"- Hours queried: {args.hours}")
    print(f"- Tickets found: {result.get('count', len(tickets))}")
    print(f"- Page: {result.get('page')}")
    print(f"- Per page: {result.get('per_page')}")
    print(f"- Has more: {result.get('has_more')}")

    if not tickets:
        print("- No tickets returned in this window.")
        return 0

    print()
    print("Sample tickets:")
    for ticket in tickets[: args.show]:
        print(
            f"- ID: {ticket.get('id')} | "
            f"Created: {ticket.get('created_at')} | "
            f"Updated: {ticket.get('updated_at')} | "
            f"Subject: {ticket.get('subject')}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
