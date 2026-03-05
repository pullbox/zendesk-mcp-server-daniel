from typing import Any

from zendesk_mcp_server.ticket_field_metadata import (
    RELEVANT_TICKET_FIELD_ALIASES,
    TicketFieldOptionResolver,
    normalize_field_value,
)


def apply_ticket_field_displays(
    ticket_payload: dict[str, Any],
    option_resolver: TicketFieldOptionResolver,
) -> dict[str, Any]:
    ticket = ticket_payload.get("ticket") if isinstance(ticket_payload.get("ticket"), dict) else ticket_payload
    if not isinstance(ticket, dict):
        return ticket_payload

    custom_fields = ticket.get("custom_fields")
    if not isinstance(custom_fields, dict):
        return ticket_payload

    normalized_custom_fields: dict[str, Any] = {}
    for raw_name, raw_value in custom_fields.items():
        normalized_name = normalize_field_value(raw_name)
        if isinstance(normalized_name, str) and normalized_name not in normalized_custom_fields:
            normalized_custom_fields[normalized_name] = raw_value

    filtered_custom_fields: dict[str, Any] = {}
    for source_name, output_name in RELEVANT_TICKET_FIELD_ALIASES.items():
        if source_name in custom_fields:
            raw_value = custom_fields[source_name]
        else:
            normalized_source_name = normalize_field_value(source_name)
            if not isinstance(normalized_source_name, str) or normalized_source_name not in normalized_custom_fields:
                continue
            raw_value = normalized_custom_fields[normalized_source_name]

        if output_name == "Escalation Status":
            translated_value = option_resolver.translate(output_name, raw_value)
            ticket["escalation_status_tag"] = raw_value
            ticket["escalation_status_display"] = translated_value
            filtered_custom_fields[output_name] = translated_value
            continue

        filtered_custom_fields[output_name] = option_resolver.translate(output_name, raw_value)

    ticket["custom_fields"] = filtered_custom_fields
    ticket.pop("_raw_custom_fields", None)
    return ticket_payload
