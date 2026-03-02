import asyncio
import importlib
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, unquote, urlparse

from mcp.types import CallToolRequest, CallToolRequestParams

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zendesk_mcp_server.zendesk_client import ZendeskClient


class TestGetTicketsLastFiveHours(unittest.TestCase):
    def setUp(self) -> None:
        zenpy_patcher = patch("zendesk_mcp_server.zendesk_client.Zenpy")
        self.addCleanup(zenpy_patcher.stop)
        zenpy_patcher.start()
        self.client = ZendeskClient(
            subdomain="example",
            email="agent@example.com",
            token="fake-token",
        )

    def test_get_tickets_last_five_hours_uses_search_query(self) -> None:
        fixed_now = datetime(2026, 3, 2, 15, 30, 0, tzinfo=timezone.utc)
        api_payload = {
            "results": [
                {
                    "result_type": "ticket",
                    "id": 101,
                    "subject": "New billing issue",
                    "status": "open",
                    "priority": "high",
                    "description": "Customer cannot update card",
                    "created_at": "2026-03-02T13:00:00Z",
                    "updated_at": "2026-03-02T14:45:00Z",
                    "requester_id": 2001,
                    "assignee_id": 3001,
                    "organization_id": 4001,
                    "custom_fields": [{"id": 9001, "value": "billing"}],
                },
                {
                    "result_type": "user",
                    "id": 999,
                },
            ],
            "next_page": None,
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with (
            patch("zendesk_mcp_server.zendesk_client.datetime") as mock_datetime,
            patch("zendesk_mcp_server.zendesk_client.urllib.request.urlopen", mock_urlopen),
            patch.object(self.client, "_resolve_custom_fields", return_value={"Team": "billing"}),
        ):
            mock_datetime.now.return_value = fixed_now

            result = self.client.get_tickets(last_hours=5, per_page=25)

        request = mock_urlopen.call_args.args[0]
        parsed_url = urlparse(request.full_url)
        params = parse_qs(parsed_url.query)
        query = unquote(params["query"][0])

        self.assertEqual(parsed_url.path, "/api/v2/search.json")
        self.assertIn("type:ticket", query)
        self.assertIn("updated>2026-03-02T10:30:00+00:00", query)
        self.assertEqual(params["page"][0], "1")
        self.assertEqual(params["per_page"][0], "25")
        self.assertEqual(result["count"], 1)
        self.assertFalse(result["has_more"])
        self.assertEqual(result["filters"]["last_hours"], 5)
        self.assertEqual(
            result["tickets"][0],
            {
                "id": 101,
                "subject": "New billing issue",
                "status": "open",
                "priority": "high",
                "description": "Customer cannot update card",
                "created_at": "2026-03-02T13:00:00Z",
                "updated_at": "2026-03-02T14:45:00Z",
                "requester_id": 2001,
                "assignee_id": 3001,
                "organization_id": 4001,
                "custom_fields": {"Team": "billing"},
            },
        )


class TestServerGetTicketsLastFiveHours(unittest.TestCase):
    def test_get_tickets_tool_emits_structured_content(self) -> None:
        client_payload = {
            "tickets": [{"id": 101, "subject": "New billing issue"}],
            "count": 1,
            "page": 1,
            "per_page": 25,
            "sort_by": "created_at",
            "sort_order": "desc",
            "filters": {
                "agent": None,
                "organization": None,
                "updated_since": None,
                "last_hours": 5,
                "stale_hours": None,
                "include_solved": False,
            },
            "has_more": False,
            "next_page": None,
            "previous_page": None,
        }

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="get_tickets",
                arguments={"last_hours": 5, "page": 1, "per_page": 25},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with patch.object(server_module, "zendesk_client") as mock_client:
            mock_client.get_tickets.return_value = client_payload

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        expected_payload = server_module.GetTicketsResult.model_validate(client_payload).model_dump(mode="json")

        mock_client.get_tickets.assert_called_once_with(
            page=1,
            per_page=25,
            sort_by="created_at",
            sort_order="desc",
            agent=None,
            organization=None,
            updated_since=None,
            last_hours=5,
            stale_hours=None,
            include_solved=False,
        )
        self.assertEqual(response.root.structuredContent, expected_payload)
        self.assertEqual(json.loads(response.root.content[0].text), expected_payload)
        self.assertFalse(response.root.isError)


if __name__ == "__main__":
    unittest.main()
