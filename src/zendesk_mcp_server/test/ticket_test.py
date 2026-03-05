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
                "created_at": "2026-03-02T13:00:00Z",
                "updated_at": "2026-03-02T14:45:00Z",
                "stale_age_hours": 0,
                "stale_age_days": 0,
            },
        )

    def test_get_tickets_created_last_hours_uses_created_query(self) -> None:
        fixed_now = datetime(2026, 3, 2, 15, 30, 0, tzinfo=timezone.utc)
        api_payload = {
            "results": [
                {
                    "result_type": "ticket",
                    "id": 901,
                    "subject": "Recent ticket",
                    "status": "open",
                    "priority": "normal",
                    "created_at": "2026-03-02T14:30:00Z",
                    "updated_at": "2026-03-02T14:45:00Z",
                }
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
        ):
            mock_datetime.now.return_value = fixed_now
            self.client.get_tickets(created_last_hours=4, per_page=25)

        request = mock_urlopen.call_args.args[0]
        parsed_url = urlparse(request.full_url)
        params = parse_qs(parsed_url.query)
        query = unquote(params["query"][0])

        self.assertIn("type:ticket", query)
        self.assertIn("created>2026-03-02T11:30:00+00:00", query)

    def test_get_ticket_includes_tags(self) -> None:
        with (
            patch.object(self.client, "_resolve_custom_fields", return_value={}),
            patch.object(
                self.client,
                "_json_get",
                return_value={
                    "ticket": {
                        "id": 777,
                        "subject": "Crash issue",
                        "status": "open",
                        "priority": "high",
                        "tags": ["crash_detected", "mobile"],
                    }
                },
            ),
        ):
            result = self.client.get_ticket(777)

        self.assertEqual(result["id"], 777)
        self.assertEqual(result["tags"], ["crash_detected", "mobile"])

    def test_get_ticket_comments_includes_attachment_metadata(self) -> None:
        payload = {
            "comments": [
                {
                    "id": 1001,
                    "author_id": 55,
                    "body": "See attached stacktrace",
                    "html_body": "<p>See attached stacktrace</p>",
                    "public": True,
                    "created_at": "2026-03-05T10:00:00Z",
                    "attachments": [
                        {
                            "id": 2001,
                            "file_name": "crash.ips",
                            "content_type": "text/plain",
                            "size": 2048,
                            "inline": False,
                        }
                    ],
                }
            ],
            "next_page": None,
        }
        with patch.object(self.client, "_json_get", return_value=payload):
            comments = self.client.get_ticket_comments(777)

        self.assertEqual(comments[0]["attachments"][0]["file_name"], "crash.ips")
        self.assertEqual(comments[0]["attachments"][0]["content_type"], "text/plain")
        self.assertEqual(comments[0]["attachments"][0]["size"], 2048)
        self.assertFalse(comments[0]["attachments"][0]["inline"])

    def test_get_tickets_stale_filter_excludes_internal_in_query(self) -> None:
        fixed_now = datetime(2026, 3, 2, 15, 30, 0, tzinfo=timezone.utc)
        api_payload = {
            "results": [
                {
                    "result_type": "ticket",
                    "id": 102,
                    "subject": "Old customer issue",
                    "status": "open",
                    "priority": "normal",
                    "created_at": "2026-02-20T09:00:00Z",
                    "updated_at": "2026-02-24T10:00:00Z",
                }
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
        ):
            mock_datetime.now.return_value = fixed_now

            result = self.client.get_tickets(stale_hours=24 * 5, exclude_internal=True, per_page=25)

        request = mock_urlopen.call_args.args[0]
        parsed_url = urlparse(request.full_url)
        params = parse_qs(parsed_url.query)
        query = unquote(params["query"][0])

        self.assertIn("status<solved", query)
        self.assertIn("updated<2026-02-25T15:30:00+00:00", query)
        self.assertIn("-tags:internal", query)
        self.assertTrue(result["filters"]["exclude_internal"])
        self.assertEqual(result["tickets"][0]["stale_age_days"], 6)
        self.assertEqual(result["tickets"][0]["stale_age_hours"], 149)

    def test_search_solved_tickets_for_agent_builds_expected_query(self) -> None:
        api_payload = {
            "count": 2,
            "results": [
                {
                    "result_type": "ticket",
                    "id": 201,
                    "subject": "Solved ticket one",
                    "status": "solved",
                    "priority": "normal",
                    "created_at": "2026-02-12T11:00:00Z",
                    "updated_at": "2026-02-14T09:00:00Z",
                    "via": {"channel": "web"},
                },
                {
                    "result_type": "ticket",
                    "id": 202,
                    "subject": "Solved ticket two",
                    "status": "solved",
                    "priority": "high",
                    "created_at": "2026-02-15T11:00:00Z",
                    "updated_at": "2026-02-18T09:00:00Z",
                    "via": {"channel": "web"},
                },
            ],
            "next_page": None,
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with patch("zendesk_mcp_server.zendesk_client.urllib.request.urlopen", mock_urlopen):
            result = self.client.search_solved_tickets_for_agent(
                agent="pedro",
                solved_after="2026-02-01",
                solved_before="2026-03-01",
                max_results=10,
            )

        request = mock_urlopen.call_args.args[0]
        parsed_url = urlparse(request.full_url)
        params = parse_qs(parsed_url.query)
        query = unquote(params["query"][0])

        self.assertEqual(parsed_url.path, "/api/v2/search.json")
        self.assertIn("type:ticket", query)
        self.assertIn("status:solved", query)
        self.assertIn("solved>=2026-02-01", query)
        self.assertIn("solved<2026-03-01", query)
        self.assertIn('assignee:"pedro"', query)
        self.assertEqual(result["total_matches"], 2)
        self.assertEqual(result["retrieved_count"], 2)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["excluded_api_created_count"], 0)
        self.assertEqual(len(result["tickets"]), 2)

    def test_search_solved_tickets_for_agent_excludes_api_created(self) -> None:
        api_payload = {
            "count": 2,
            "results": [
                {
                    "result_type": "ticket",
                    "id": 211,
                    "subject": "API-created solved ticket",
                    "status": "solved",
                    "priority": "normal",
                    "created_at": "2026-02-12T11:00:00Z",
                    "updated_at": "2026-02-14T09:00:00Z",
                    "via": {"channel": "api"},
                },
                {
                    "result_type": "ticket",
                    "id": 212,
                    "subject": "Human-created solved ticket",
                    "status": "solved",
                    "priority": "high",
                    "created_at": "2026-02-15T11:00:00Z",
                    "updated_at": "2026-02-18T09:00:00Z",
                    "via": {"channel": "web"},
                },
            ],
            "next_page": None,
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with patch("zendesk_mcp_server.zendesk_client.urllib.request.urlopen", mock_urlopen):
            result = self.client.search_solved_tickets_for_agent(
                agent="pedro",
                solved_after="2026-02-01",
                solved_before="2026-03-01",
                max_results=10,
                exclude_api_created=True,
            )

        self.assertEqual([ticket["id"] for ticket in result["tickets"]], [212])
        self.assertEqual(result["excluded_api_created_count"], 1)

    def test_search_tickets_by_text_builds_expected_query(self) -> None:
        api_payload = {
            "results": [
                {
                    "result_type": "ticket",
                    "id": 501,
                    "subject": "Facephi issue",
                    "status": "open",
                    "priority": "normal",
                    "created_at": "2026-03-01T10:00:00Z",
                    "updated_at": "2026-03-02T10:00:00Z",
                },
                {"result_type": "user", "id": 777},
            ],
            "next_page": None,
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with patch("zendesk_mcp_server.zendesk_client.urllib.request.urlopen", mock_urlopen):
            result = self.client.search_tickets_by_text(
                phrase="Facephi",
                organization="Acme",
                updated_since="2026-03-01",
                updated_before="2026-03-04",
                status="open",
                exclude_internal=True,
                comment_author="Tom",
                page=2,
                per_page=25,
            )

        request = mock_urlopen.call_args.args[0]
        parsed_url = urlparse(request.full_url)
        params = parse_qs(parsed_url.query)
        query = unquote(params["query"][0])

        self.assertEqual(parsed_url.path, "/api/v2/search.json")
        self.assertIn("type:ticket", query)
        self.assertIn('"Facephi"', query)
        self.assertIn('organization:"Acme"', query)
        self.assertIn("updated>2026-03-01", query)
        self.assertIn("updated<2026-03-04", query)
        self.assertIn("status:open", query)
        self.assertIn("-tags:internal", query)
        self.assertIn('commenter:"Tom"', query)
        self.assertEqual(params["page"][0], "2")
        self.assertEqual(params["per_page"][0], "25")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["tickets"][0]["id"], 501)

    def test_search_tickets_by_text_excludes_solved_by_default(self) -> None:
        api_payload = {"results": [], "next_page": None}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with patch("zendesk_mcp_server.zendesk_client.urllib.request.urlopen", mock_urlopen):
            self.client.search_tickets_by_text(phrase="Facephi")

        request = mock_urlopen.call_args.args[0]
        parsed_url = urlparse(request.full_url)
        params = parse_qs(parsed_url.query)
        query = unquote(params["query"][0])

        self.assertIn("status<solved", query)

    def test_search_tickets_by_text_can_include_solved(self) -> None:
        api_payload = {"results": [], "next_page": None}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with patch("zendesk_mcp_server.zendesk_client.urllib.request.urlopen", mock_urlopen):
            self.client.search_tickets_by_text(phrase="Facephi", include_solved=True)

        request = mock_urlopen.call_args.args[0]
        parsed_url = urlparse(request.full_url)
        params = parse_qs(parsed_url.query)
        query = unquote(params["query"][0])

        self.assertNotIn("status<solved", query)


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
                "exclude_internal": False,
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
            created_last_hours=None,
            stale_hours=None,
            include_solved=False,
            exclude_internal=False,
        )
        self.assertEqual(response.root.structuredContent, expected_payload)
        self.assertEqual(json.loads(response.root.content[0].text), expected_payload)
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_flags_requested_conditions(self) -> None:
        list_payload = {
            "tickets": [
                {
                    "id": 777,
                    "subject": "ACME | iOS | Crash after login",
                    "status": "solved",
                    "priority": "high",
                    "created_at": "2026-03-05T10:00:00Z",
                    "updated_at": "2026-03-05T19:30:00Z",
                    "stale_age_hours": 9,
                    "stale_age_days": 0,
                }
            ],
            "count": 1,
            "page": 1,
            "per_page": 25,
            "sort_by": "created_at",
            "sort_order": "desc",
            "filters": {
                "created_last_hours": 4,
                "exclude_internal": True,
            },
            "has_more": False,
            "next_page": None,
            "previous_page": None,
        }
        full_ticket_payload = {
            "id": 777,
            "subject": "ACME | iOS | Crash after login",
            "status": "solved",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T19:30:00Z",
            "requester_id": 1001,
            "tags": ["crash_detected"],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
            },
            "stale_age_hours": 9,
        }
        comments_payload = [
            {
                "author_id": 1001,
                "public": True,
                "body": "Any update?",
                "html_body": "<p>Any update?</p>",
                "created_at": "2026-03-05T11:00:00Z",
                "attachments": [],
            },
            {
                "author_id": 2002,
                "public": True,
                "body": "We are checking internally.",
                "html_body": "<p>We are checking internally.</p>",
                "created_at": "2026-03-05T11:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "Still broken",
                "html_body": "<p>Still broken</p>",
                "created_at": "2026-03-05T12:00:00Z",
                "attachments": [],
            },
        ]

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="scan_tickets_in_trouble",
                arguments={"created_last_hours": 4, "per_page": 25},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with (
            patch.object(server_module, "zendesk_client") as mock_client,
            patch.object(server_module, "_prepare_ticket_payload", return_value=full_ticket_payload),
        ):
            mock_client.get_tickets.return_value = list_payload
            mock_client.get_ticket_comments.return_value = comments_payload

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual(structured["scanned_count"], 1)
        self.assertEqual(structured["in_trouble_count"], 1)
        flag_codes = [flag["code"] for flag in structured["tickets"][0]["flags"]]
        self.assertEqual(flag_codes[0], "crash_process_gap")
        self.assertIn("customer_comment_no_response", flag_codes)
        self.assertIn("solved_without_customer_confirmation", flag_codes)
        self.assertIn("high_priority_no_recent_updates", flag_codes)
        self.assertEqual(structured["tickets"][0]["risk_score"], 100)
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_orders_tickets_by_weighted_risk(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 200, "subject": "Login issue", "status": "open", "priority": "normal"},
                {"id": 100, "subject": "ACME | Android | Crash", "status": "open", "priority": "high"},
            ],
            "count": 2,
            "page": 1,
            "per_page": 25,
            "sort_by": "created_at",
            "sort_order": "desc",
            "filters": {
                "created_last_hours": 4,
                "exclude_internal": True,
            },
            "has_more": False,
            "next_page": None,
            "previous_page": None,
        }
        full_ticket_by_id = {
            200: {
                "id": 200,
                "subject": "Login issue",
                "status": "open",
                "priority": "normal",
                "created_at": "2026-03-05T10:00:00Z",
                "updated_at": "2026-03-05T10:20:00Z",
                "requester_id": 1001,
                "tags": [],
                "custom_fields": {
                    "Status With": "customer",
                    "Support Stage": "investigation",
                    "Release Stage": "n/a",
                },
            },
            100: {
                "id": 100,
                "subject": "ACME | Android | Crash",
                "status": "open",
                "priority": "high",
                "created_at": "2026-03-05T10:00:00Z",
                "updated_at": "2026-03-05T19:00:00Z",
                "requester_id": 1002,
                "tags": [],
                "custom_fields": {},
                "stale_age_hours": 9,
            },
        }
        comments_by_id = {
            200: [
                {
                    "author_id": 2002,
                    "public": True,
                    "body": "We are investigating.",
                    "html_body": "<p>We are investigating.</p>",
                    "created_at": "2026-03-05T10:20:00Z",
                    "attachments": [],
                }
            ],
            100: [],
        }

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="scan_tickets_in_trouble",
                arguments={"created_last_hours": 4, "per_page": 25},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with (
            patch.object(server_module, "zendesk_client") as mock_client,
            patch.object(
                server_module,
                "_prepare_ticket_payload",
                side_effect=lambda ticket_id: full_ticket_by_id[ticket_id],
            ),
        ):
            mock_client.get_tickets.return_value = list_payload
            mock_client.get_ticket_comments.side_effect = lambda ticket_id: comments_by_id[ticket_id]

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual([ticket["ticket_id"] for ticket in structured["tickets"]], [100, 200])
        self.assertGreater(structured["tickets"][0]["risk_score"], structured["tickets"][1]["risk_score"])
        self.assertEqual(structured["tickets"][0]["flags"][0]["code"], "missing_initial_response")
        self.assertFalse(response.root.isError)

    def test_sample_solved_tickets_for_agent_is_deterministic_with_seed(self) -> None:
        client_payload = {
            "tickets": [
                {"id": 301, "subject": "A", "status": "solved", "priority": "normal"},
                {"id": 302, "subject": "B", "status": "solved", "priority": "normal"},
                {"id": 303, "subject": "C", "status": "solved", "priority": "normal"},
            ],
            "total_matches": 3,
            "retrieved_count": 3,
            "truncated": False,
            "excluded_api_created_count": 0,
        }

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="sample_solved_tickets_for_agent",
                arguments={
                    "agent": "pedro",
                    "solved_after": "2026-02-01",
                    "solved_before": "2026-03-01",
                    "count": 2,
                    "exclude_api_created": True,
                    "seed": 7,
                    "max_pool": 50,
                },
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with patch.object(server_module, "zendesk_client") as mock_client:
            mock_client.search_solved_tickets_for_agent.return_value = client_payload

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        expected_ids = [302, 301]
        structured = response.root.structuredContent

        mock_client.search_solved_tickets_for_agent.assert_called_once_with(
            agent="pedro",
            solved_after="2026-02-01",
            solved_before="2026-03-01",
            max_results=50,
            exclude_api_created=True,
        )
        self.assertEqual([ticket["id"] for ticket in structured["tickets"]], expected_ids)
        self.assertEqual(structured["sampled_count"], 2)
        self.assertEqual(structured["total_matches"], 3)
        self.assertFalse(structured["truncated"])
        self.assertTrue(structured["exclude_api_created"])
        self.assertEqual(structured["excluded_api_created_count"], 0)
        self.assertFalse(response.root.isError)

    def test_search_tickets_by_text_tool_emits_structured_content(self) -> None:
        client_payload = {
            "tickets": [{"id": 501, "subject": "Facephi issue"}],
            "page": 1,
            "per_page": 25,
            "count": 1,
            "sort_by": "updated_at",
            "sort_order": "desc",
            "query": 'type:ticket "Facephi" commenter:"Tom"',
            "filters": {
                "phrase": "Facephi",
                "organization": None,
                "updated_since": "2026-03-01T00:00:00+00:00",
                "updated_before": None,
                "status": None,
                "include_solved": False,
                "exclude_internal": False,
                "comment_author": "Tom",
            },
            "has_more": False,
            "next_page": None,
            "previous_page": None,
        }

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="search_tickets_by_text",
                arguments={
                    "phrase": "Facephi",
                    "comment_author": "Tom",
                    "updated_since": "2026-03-01T00:00:00+00:00",
                },
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with patch.object(server_module, "zendesk_client") as mock_client:
            mock_client.search_tickets_by_text.return_value = client_payload

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        expected_payload = server_module.SearchTicketsByTextResult.model_validate(client_payload).model_dump(mode="json")

        mock_client.search_tickets_by_text.assert_called_once_with(
            phrase="Facephi",
            page=1,
            per_page=25,
            sort_by="updated_at",
            sort_order="desc",
            organization=None,
            updated_since="2026-03-01T00:00:00+00:00",
            updated_before=None,
            status=None,
            include_solved=False,
            exclude_internal=False,
            comment_author="Tom",
        )
        self.assertEqual(response.root.structuredContent, expected_payload)
        self.assertEqual(json.loads(response.root.content[0].text), expected_payload)
        self.assertFalse(response.root.isError)


if __name__ == "__main__":
    unittest.main()
