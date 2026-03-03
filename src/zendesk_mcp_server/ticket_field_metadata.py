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

    def load(self) -> None:
        try:
            field_definitions = self.zendesk_client.get_ticket_field_definitions()
        except Exception as exc:
            logger.warning(f"Failed to load ticket field definitions: {exc}")
            self.option_maps = {}
            return

        option_maps: dict[str, dict[str, str]] = {}
        for field in field_definitions:
            title = field.get("title")
            if title not in RELEVANT_TICKET_FIELD_ALIASES:
                continue

            canonical_title = RELEVANT_TICKET_FIELD_ALIASES[title]
            field_id = field.get("id")
            if field_id is None:
                continue

            try:
                options = self.zendesk_client.get_ticket_field_options(int(field_id))
            except Exception as exc:
                logger.warning(f"Failed to load options for ticket field '{title}' ({field_id}): {exc}")
                continue

            value_map = {}
            for option in options:
                raw_value = option.get("value")
                display_name = option.get("name")
                normalized_value = normalize_field_value(raw_value)
                if normalized_value and isinstance(display_name, str):
                    value_map[normalized_value] = display_name

            if value_map:
                option_maps[canonical_title] = value_map

        self.option_maps = option_maps
        logger.info(f"Loaded ticket field option maps for {len(option_maps)} relevant fields")

    def translate(self, field_name: str, raw_value: Any) -> Any:
        normalized_value = normalize_field_value(raw_value)
        field_options = self.option_maps.get(field_name, {})
        if normalized_value in field_options:
            return field_options[normalized_value]
        return humanize_field_value(raw_value)
