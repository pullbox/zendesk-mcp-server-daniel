from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger("zendesk-mcp-client")


class TicketsCrudRepository:
    def __init__(
        self,
        *,
        base_url: str,
        json_get: Callable[[str], Dict[str, Any]],
        resolve_custom_fields: Callable[[list], Dict[str, Any]],
        zenpy_client: Any,
        ticket_factory: Callable[..., Any],
    ) -> None:
        self.base_url = base_url
        self._json_get = json_get
        self._resolve_custom_fields = resolve_custom_fields
        self._zenpy_client = zenpy_client
        self._ticket_factory = ticket_factory

    def get_ticket(self, ticket_id: int) -> Dict[str, Any]:
        logger.info("Fetching Zendesk ticket %s", ticket_id)
        data = self._json_get(f"{self.base_url}/tickets/{ticket_id}.json")
        ticket = data.get("ticket", {})
        custom_fields = self._resolve_custom_fields(ticket.get("custom_fields", []))
        result = {
            "id": ticket.get("id"),
            "subject": ticket.get("subject"),
            "description": ticket.get("description"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "requester_id": ticket.get("requester_id"),
            "assignee_id": ticket.get("assignee_id"),
            "organization_id": ticket.get("organization_id"),
            "tags": ticket.get("tags", []),
            "custom_fields": custom_fields,
        }
        logger.info("Fetched Zendesk ticket %s successfully", ticket_id)
        return result

    def create_ticket(
        self,
        *,
        subject: str,
        description: str,
        requester_id: int | None = None,
        assignee_id: int | None = None,
        priority: str | None = None,
        type: str | None = None,
        tags: List[str] | None = None,
        custom_fields: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        ticket = self._ticket_factory(
            subject=subject,
            description=description,
            requester_id=requester_id,
            assignee_id=assignee_id,
            priority=priority,
            type=type,
            tags=tags,
            custom_fields=custom_fields,
        )
        created_audit = self._zenpy_client.tickets.create(ticket)

        created_ticket_id = getattr(getattr(created_audit, "ticket", None), "id", None)
        if created_ticket_id is None:
            created_ticket_id = getattr(created_audit, "id", None)

        created = self._zenpy_client.tickets(id=created_ticket_id) if created_ticket_id else None

        return {
            "id": getattr(created, "id", created_ticket_id),
            "subject": getattr(created, "subject", subject),
            "description": getattr(created, "description", description),
            "status": getattr(created, "status", "new"),
            "priority": getattr(created, "priority", priority),
            "type": getattr(created, "type", type),
            "created_at": str(getattr(created, "created_at", "")),
            "updated_at": str(getattr(created, "updated_at", "")),
            "requester_id": getattr(created, "requester_id", requester_id),
            "assignee_id": getattr(created, "assignee_id", assignee_id),
            "organization_id": getattr(created, "organization_id", None),
            "tags": list(getattr(created, "tags", tags or []) or []),
        }

    def update_ticket(self, ticket_id: int, fields: Dict[str, Any]) -> Dict[str, Any]:
        ticket = self._zenpy_client.tickets(id=ticket_id)
        for key, value in fields.items():
            if value is None:
                continue
            setattr(ticket, key, value)

        self._zenpy_client.tickets.update(ticket)
        refreshed = self._zenpy_client.tickets(id=ticket_id)

        return {
            "id": refreshed.id,
            "subject": refreshed.subject,
            "description": refreshed.description,
            "status": refreshed.status,
            "priority": refreshed.priority,
            "type": getattr(refreshed, "type", None),
            "created_at": str(refreshed.created_at),
            "updated_at": str(refreshed.updated_at),
            "requester_id": refreshed.requester_id,
            "assignee_id": refreshed.assignee_id,
            "organization_id": refreshed.organization_id,
            "tags": list(getattr(refreshed, "tags", []) or []),
        }
