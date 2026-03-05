from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("zendesk-mcp-client")


def format_zendesk_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.replace(microsecond=0).isoformat()


def parse_zendesk_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Could not parse Zendesk timestamp: %s", value)
        return None


def build_ticket_list_item(ticket: Dict[str, Any], now: datetime, agent_ticket_base_url: str) -> Dict[str, Any]:
    updated_at = ticket.get("updated_at")
    updated_dt = parse_zendesk_datetime(updated_at)

    ticket_id = ticket.get("id")
    ticket_url = f"{agent_ticket_base_url}/{ticket_id}" if ticket_id is not None else None

    stale_age_hours = None
    stale_age_days = None
    if updated_dt is not None:
        age_seconds = max((now - updated_dt).total_seconds(), 0)
        stale_age_hours = int(age_seconds // 3600)
        stale_age_days = int(age_seconds // 86400)

    return {
        "id": ticket_id,
        "ticket_url": ticket_url,
        "ticket_link": f"[{ticket_id}]({ticket_url})" if ticket_url is not None else None,
        "subject": ticket.get("subject"),
        "status": ticket.get("status"),
        "priority": ticket.get("priority"),
        "created_at": ticket.get("created_at"),
        "updated_at": updated_at,
        "stale_age_hours": stale_age_hours,
        "stale_age_days": stale_age_days,
    }
