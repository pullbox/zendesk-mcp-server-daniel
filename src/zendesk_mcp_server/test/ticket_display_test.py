import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from zendesk_mcp_server.ticket_display import apply_ticket_field_displays
from zendesk_mcp_server.ticket_field_metadata import TicketFieldOptionResolver


class StubZendeskClient:
    def get_ticket_field_definitions(self):
        return [
            {"id": 1, "title": "Escalation Status"},
            {"id": 2, "title": "Support Stage"},
            {"id": 3, "title": "Release Stage"},
            {"id": 4, "title": "Support Class"},
            {"id": 5, "title": "Eng Priority"},
        ]

    def get_ticket_field_options(self, ticket_field_id: int):
        options = {
            1: [{"value": "esc_7", "name": "Sol. Delivered"}],
            2: [{"value": "validate_resoution", "name": "Validate Resolution"}],
            3: [{"value": "testing_-_pre-release_uat", "name": "Testing / Pre-Release UAT"}],
            4: [{"value": "dev", "name": "DevEng"}],
            5: [{"value": "2", "name": "2"}],
        }
        return options.get(ticket_field_id, [])


class TestTicketFieldDisplay(unittest.TestCase):
    def test_apply_ticket_field_displays_translates_relevant_fields(self) -> None:
        resolver = TicketFieldOptionResolver(StubZendeskClient())
        resolver.load()

        payload = {
            "id": 42414,
            "custom_fields": {
                "Support Stage": "validate_resoution",
                "Release Stage": "testing_-_pre-release_uat",
                "Escalation Status": "esc_7",
                "Support Class": "dev",
                "Eng Priority": "2",
                "Ignored Field": "ignore-me",
            },
        }

        result = apply_ticket_field_displays(payload, resolver)

        self.assertEqual(
            result["custom_fields"],
            {
                "Support Stage": "Validate Resolution",
                "Release Stage": "Testing / Pre-Release UAT",
                "Escalation Status": "Sol. Delivered",
                "Support Class": "DevEng",
                "Priority": "2",
            },
        )
        self.assertEqual(result["escalation_status_tag"], "esc_7")
        self.assertEqual(result["escalation_status_display"], "Sol. Delivered")


if __name__ == "__main__":
    unittest.main()
