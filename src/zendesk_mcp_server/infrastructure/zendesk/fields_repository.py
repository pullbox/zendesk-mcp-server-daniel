from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger("zendesk-mcp-client")


class FieldsRepository:
    def __init__(self, *, base_url: str, json_get: Callable[[str], Dict[str, Any]]) -> None:
        self.base_url = base_url
        self._json_get = json_get

    def get_ticket_fields(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/ticket_fields.json"
        logger.info("Fetching Zendesk ticket fields")
        data = self._json_get(url)
        logger.info("Fetched Zendesk ticket fields successfully")
        return [
            {
                "id": field.get("id"),
                "title": field.get("title"),
                "type": field.get("type"),
                "active": field.get("active"),
            }
            for field in data.get("ticket_fields", [])
        ]

    def get_ticket_field_definitions(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/ticket_fields.json"
        logger.info("Fetching Zendesk ticket field definitions")
        data = self._json_get(url)
        logger.info("Fetched Zendesk ticket field definitions successfully")
        return data.get("ticket_fields", [])

    def get_ticket_field_options(self, ticket_field_id: int) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/ticket_fields/{ticket_field_id}/options.json"
        logger.info("Fetching Zendesk ticket field options for field %s", ticket_field_id)
        data = self._json_get(url)
        logger.info("Fetched Zendesk ticket field options for field %s successfully", ticket_field_id)
        return data.get("custom_field_options", [])
