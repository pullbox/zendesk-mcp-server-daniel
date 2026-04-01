import logging
from typing import Any


logger = logging.getLogger("zendesk-ticket-fields")

RELEVANT_TICKET_FIELD_ALIASES = {
    "Status With": "Status With",
    "Support Stage": "Support Stage",
    "Release Stage": "Release Stage",
    "Escalation": "Escalation Status",
    "Escalation Status": "Escalation Status",
    "Support Class": "Support Class",
    "Eng Priority": "Priority",
    "Priority": "Priority",
}

OPTION_BACKED_TICKET_FIELDS = {
    "Status With",
    "Support Stage",
    "Release Stage",
    "Escalation",
    "Escalation Status",
    "Support Class",
}


def normalize_field_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def humanize_field_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    text = value.replace("_-_", " / ").replace("_", " ").strip()
    if not text:
        return value

    words = [word.capitalize() if word.isalpha() else word for word in text.split()]
    return " ".join(words)


class TicketFieldOptionResolver:
    def __init__(self, zendesk_client: Any):
        self.zendesk_client = zendesk_client
        self.option_maps: dict[str, dict[str, str]] = {}
        self._field_id_map: dict[str, int] = {}
        self._reverse_option_maps: dict[str, dict[str, str]] = {}  # canonical_name -> {display_name -> raw_value}

    def load(self) -> None:
        try:
            field_definitions = self.zendesk_client.get_ticket_field_definitions()
        except Exception as exc:
            logger.warning(f"Failed to load ticket field definitions: {exc}")
            self.option_maps = {}
            return

        option_maps: dict[str, dict[str, str]] = {}
        field_id_map: dict[str, int] = {}
        reverse_option_maps: dict[str, dict[str, str]] = {}
        for field in field_definitions:
            title = field.get("title")
            if title not in RELEVANT_TICKET_FIELD_ALIASES:
                continue
            if title not in OPTION_BACKED_TICKET_FIELDS:
                continue

            canonical_title = RELEVANT_TICKET_FIELD_ALIASES[title]
            field_id = field.get("id")
            if field_id is None:
                continue

            field_id_map[canonical_title] = int(field_id)

            try:
                options = self.zendesk_client.get_ticket_field_options(int(field_id))
            except Exception as exc:
                logger.warning(f"Failed to load options for ticket field '{title}' ({field_id}): {exc}")
                continue

            value_map = {}
            reverse_map = {}
            for option in options:
                raw_value = option.get("value")
                display_name = option.get("name")
                normalized_value = normalize_field_value(raw_value)
                if normalized_value and isinstance(display_name, str):
                    value_map[normalized_value] = display_name
                if raw_value and isinstance(display_name, str):
                    reverse_map[display_name] = raw_value

            if value_map:
                option_maps[canonical_title] = value_map
            if reverse_map:
                reverse_option_maps[canonical_title] = reverse_map

        self.option_maps = option_maps
        self._field_id_map = field_id_map
        self._reverse_option_maps = reverse_option_maps
        logger.info(f"Loaded ticket field option maps for {len(option_maps)} relevant fields")

    def get_escalation_status_search_terms(self, display_names: set[str]) -> list[str]:
        """Return Zendesk custom_field search terms for the given escalation status display names."""
        field_id = self._field_id_map.get("Escalation Status")
        if field_id is None:
            return []
        reverse_map = self._reverse_option_maps.get("Escalation Status", {})
        terms = []
        for display_name in sorted(display_names):
            raw_value = reverse_map.get(display_name)
            if raw_value:
                terms.append(f"custom_field_{field_id}:{raw_value}")
        return terms

    def translate(self, field_name: str, raw_value: Any) -> Any:
        normalized_value = normalize_field_value(raw_value)
        field_options = self.option_maps.get(field_name, {})
        if normalized_value in field_options:
            return field_options[normalized_value]
        return humanize_field_value(raw_value)
