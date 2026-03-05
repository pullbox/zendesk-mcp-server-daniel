from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger("zendesk-mcp-client")


class FieldValueMapper:
    def __init__(self, *, get_ticket_fields: Callable[[], List[Dict[str, Any]]]) -> None:
        self._get_ticket_fields = get_ticket_fields
        self._field_map_cache: Dict[int, str] | None = None

    def get_field_map(self) -> Dict[int, str]:
        if self._field_map_cache is None:
            try:
                fields = self._get_ticket_fields()
                self._field_map_cache = {field["id"]: field["title"] for field in fields}
            except Exception as exc:
                logger.warning("Could not load ticket field map (custom fields will show IDs): %s", exc)
                self._field_map_cache = {}
        return self._field_map_cache

    def resolve_custom_fields(self, raw: list) -> Dict[str, Any]:
        if not raw:
            return {}
        field_map = self.get_field_map()
        return {
            field_map.get(custom_field["id"], str(custom_field["id"])): custom_field["value"]
            for custom_field in raw
            if custom_field.get("value") is not None
        }
