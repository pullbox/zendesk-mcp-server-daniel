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
                "ticket_url": "https://example.zendesk.com/agent/tickets/101",
                "ticket_link": "[101](https://example.zendesk.com/agent/tickets/101)",
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
            "count": 4,
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
                    "id": 2020,
                    "subject": "Closed ticket",
                    "status": "closed",
                    "priority": "high",
                    "created_at": "2026-02-15T11:00:00Z",
                    "updated_at": "2026-02-18T09:00:00Z",
                    "via": {"channel": "web"},
                },
                {
                    "result_type": "ticket",
                    "id": 2021,
                    "subject": "Solved ticket two",
                    "status": "solved",
                    "priority": "high",
                    "created_at": "2026-02-15T11:00:00Z",
                    "updated_at": "2026-02-18T09:00:00Z",
                    "via": {"channel": "web"},
                },
                {
                    "result_type": "ticket",
                    "id": 2022,
                    "subject": "Pending ticket",
                    "status": "pending",
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
        self.assertIn("updated>=2026-02-01", query)
        self.assertIn("updated<2026-03-01", query)
        self.assertIn('assignee:"pedro"', query)
        self.assertEqual(result["total_matches"], 3)
        self.assertEqual(result["retrieved_count"], 3)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["excluded_api_created_count"], 0)
        self.assertEqual([ticket["id"] for ticket in result["tickets"]], [201, 2020, 2021])

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

    def test_search_solved_tickets_for_agent_resolves_name_to_assignee_id_when_requested(self) -> None:
        api_payload = {
            "count": 0,
            "results": [],
            "next_page": None,
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with (
            patch("zendesk_mcp_server.zendesk_client.urllib.request.urlopen", mock_urlopen),
            patch.object(self.client, "resolve_user", return_value={"id": 3001}),
        ):
            self.client.search_solved_tickets_for_agent(
                agent="pedro",
                solved_after="2026-02-01",
                solved_before="2026-03-01",
                resolve_agent_id=True,
            )

        request = mock_urlopen.call_args.args[0]
        parsed_url = urlparse(request.full_url)
        params = parse_qs(parsed_url.query)
        query = unquote(params["query"][0])

        self.assertIn("assignee_id:3001", query)

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

    def test_get_tickets_resolves_agent_email_to_assignee_id(self) -> None:
        fixed_now = datetime(2026, 3, 2, 15, 30, 0, tzinfo=timezone.utc)
        api_payload = {"results": [], "next_page": None}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with (
            patch("zendesk_mcp_server.zendesk_client.datetime") as mock_datetime,
            patch("zendesk_mcp_server.zendesk_client.urllib.request.urlopen", mock_urlopen),
            patch.object(self.client, "resolve_user", return_value={"id": 3001}),
        ):
            mock_datetime.now.return_value = fixed_now
            self.client.get_tickets(agent="agent@example.com", per_page=25)

        request = mock_urlopen.call_args.args[0]
        parsed_url = urlparse(request.full_url)
        params = parse_qs(parsed_url.query)
        query = unquote(params["query"][0])

        self.assertIn("assignee_id:3001", query)

    def test_search_tickets_by_text_resolves_comment_author_email_to_commenter_id(self) -> None:
        api_payload = {"results": [], "next_page": None}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with (
            patch("zendesk_mcp_server.zendesk_client.urllib.request.urlopen", mock_urlopen),
            patch.object(self.client, "resolve_user", return_value={"id": 4002}),
        ):
            self.client.search_tickets_by_text(
                phrase="Facephi",
                comment_author="requester@example.com",
            )

        request = mock_urlopen.call_args.args[0]
        parsed_url = urlparse(request.full_url)
        params = parse_qs(parsed_url.query)
        query = unquote(params["query"][0])

        self.assertIn("commenter:4002", query)


class TestServerGetTicketsLastFiveHours(unittest.TestCase):
    def test_prepare_ticket_payload_adds_ticket_link_fields(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with (
            patch.object(server_module, "zendesk_client") as mock_client,
            patch.object(
                server_module,
                "apply_ticket_field_displays",
                side_effect=lambda ticket, _resolver: ticket,
            ),
        ):
            mock_client.get_ticket.return_value = {
                "id": 42484,
                "subject": "ACME | iOS | Crash",
            }
            payload = server_module._prepare_ticket_payload(42484)

        self.assertEqual(payload["ticket_url"], "https://appdomesupport.zendesk.com/agent/tickets/42484")
        self.assertEqual(payload["ticket_link"], "[42484](https://appdomesupport.zendesk.com/agent/tickets/42484)")

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

    def test_scan_tickets_in_trouble_excludes_resolved_tickets_from_scan(self) -> None:
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
                },
                {
                    "id": 778,
                    "subject": "ACME | Android | Closed duplicate",
                    "status": "closed",
                    "priority": "high",
                    "created_at": "2026-03-05T10:00:00Z",
                    "updated_at": "2026-03-05T19:30:00Z",
                    "stale_age_hours": 9,
                    "stale_age_days": 0,
                },
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
            patch.object(server_module, "_prepare_ticket_payload") as mock_prepare_ticket_payload,
        ):
            mock_client.get_tickets.return_value = list_payload

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual(structured["scanned_count"], 0)
        self.assertEqual(structured["in_trouble_count"], 0)
        self.assertEqual(structured["tickets"], [])
        mock_prepare_ticket_payload.assert_not_called()
        mock_client.get_ticket_comments.assert_not_called()
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_excludes_ticket_closed_by_fetch_time(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 779, "subject": "ACME | iOS | Login issue", "status": "open", "priority": "normal"},
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
            "id": 779,
            "subject": "ACME | iOS | Login issue",
            "status": "closed",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:20:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "resolved",
                "Release Stage": "n/a",
            },
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
            patch.object(server_module, "_prepare_ticket_payload", return_value=full_ticket_payload),
        ):
            mock_client.get_tickets.return_value = list_payload

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual(structured["scanned_count"], 0)
        self.assertEqual(structured["in_trouble_count"], 0)
        self.assertEqual(structured["tickets"], [])
        mock_client.get_ticket_comments.assert_not_called()
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

    def test_scan_tickets_in_trouble_includes_markdown_ticket_list(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 100, "subject": "ACME | Android | Crash", "status": "open", "priority": "high"},
            ],
            "count": 1,
            "page": 1,
            "per_page": 25,
            "sort_by": "created_at",
            "sort_order": "desc",
            "filters": {
                "created_last_hours": 12,
                "exclude_internal": True,
            },
            "has_more": False,
            "next_page": None,
            "previous_page": None,
        }
        full_ticket_payload = {
            "id": 100,
            "subject": "ACME | Android | Crash",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T12:30:00Z",
            "requester_id": 1002,
            "tags": [],
            "custom_fields": {},
        }

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="scan_tickets_in_trouble",
                arguments={"created_last_hours": 12, "per_page": 25},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with (
            patch.object(server_module, "zendesk_client") as mock_client,
            patch.object(server_module, "_prepare_ticket_payload", return_value=full_ticket_payload),
        ):
            mock_client.get_tickets.return_value = list_payload
            mock_client.get_ticket_comments.return_value = []

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertIn("[100](https://appdomesupport.zendesk.com/agent/tickets/100)", structured["ticket_list_markdown"])
        self.assertIn("ACME | Android | Crash", structured["ticket_list_markdown"])
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_ignores_new_ticket_without_overdue_initial_response(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 910, "subject": "ACME | iOS | Login issue", "status": "new", "priority": "normal"},
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
            "id": 910,
            "subject": "ACME | iOS | Login issue",
            "status": "new",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "intake",
                "Release Stage": "n/a",
            },
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
            patch.object(server_module, "_prepare_ticket_payload", return_value=full_ticket_payload),
        ):
            mock_client.get_tickets.return_value = list_payload
            mock_client.get_ticket_comments.return_value = []

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual(structured["scanned_count"], 0)
        self.assertEqual(structured["in_trouble_count"], 0)
        self.assertEqual(structured["tickets"], [])
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_includes_new_ticket_when_no_response_over_1h(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 911, "subject": "ACME | iOS | Login issue", "status": "new", "priority": "normal"},
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
            "id": 911,
            "subject": "ACME | iOS | Login issue",
            "status": "new",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T12:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "intake",
                "Release Stage": "n/a",
            },
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
            patch.object(server_module, "_prepare_ticket_payload", return_value=full_ticket_payload),
        ):
            mock_client.get_tickets.return_value = list_payload
            mock_client.get_ticket_comments.return_value = []

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual(structured["scanned_count"], 1)
        self.assertEqual(structured["in_trouble_count"], 1)
        flag_codes = [flag["code"] for flag in structured["tickets"][0]["flags"]]
        self.assertIn("missing_initial_response", flag_codes)
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_skips_initial_response_flag_when_first_comment_is_internal(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 9112, "subject": "ACME | iOS | Login issue", "status": "new", "priority": "normal"},
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
            "id": 9112,
            "subject": "ACME | iOS | Login issue",
            "status": "new",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T12:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "intake",
                "Release Stage": "n/a",
            },
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": False,
                "body": "Created internally on behalf of customer.",
                "html_body": "<p>Created internally on behalf of customer.</p>",
                "created_at": "2026-03-05T10:00:00Z",
                "attachments": [],
            }
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
        self.assertEqual(structured["scanned_count"], 0)
        self.assertEqual(structured["in_trouble_count"], 0)
        self.assertEqual(structured["tickets"], [])
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_mentions_recent_call_and_datetime_comments(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 912, "subject": "ACME | iOS | Login issue", "status": "open", "priority": "normal"},
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
            "id": 912,
            "subject": "ACME | iOS | Login issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T12:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Initial troubleshooting done.",
                "html_body": "<p>Initial troubleshooting done.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 2002,
                "public": True,
                "body": "Let's schedule a call on 2026-03-10 at 14:30.",
                "html_body": "<p>Let's schedule a call on 2026-03-10 at 14:30.</p>",
                "created_at": "2026-03-05T11:20:00Z",
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
        notes = structured["tickets"][0]["recent_comment_notes"]
        joined_notes = " ".join(notes).lower()
        self.assertIn("call/scheduling", joined_notes)
        self.assertIn("2026-03-10", joined_notes)
        self.assertIn("14:30", joined_notes)
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_flags_missing_public_meeting_summary_from_assignee(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 913, "subject": "ACME | iOS | Login issue", "status": "open", "priority": "normal"},
            ],
            "count": 1,
            "page": 1,
            "per_page": 25,
            "sort_by": "created_at",
            "sort_order": "desc",
            "filters": {
                "created_last_hours": 24,
                "exclude_internal": True,
            },
            "has_more": False,
            "next_page": None,
            "previous_page": None,
        }
        full_ticket_payload = {
            "id": 913,
            "subject": "ACME | iOS | Login issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-10T16:00:00Z",
            "requester_id": 1001,
            "assignee_id": 2002,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Let's schedule a call on 2026-03-10 at 14:30.",
                "html_body": "<p>Let's schedule a call on 2026-03-10 at 14:30.</p>",
                "created_at": "2026-03-05T11:20:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "That works for us, thanks.",
                "html_body": "<p>That works for us, thanks.</p>",
                "created_at": "2026-03-05T11:30:00Z",
                "attachments": [],
            },
        ]

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="scan_tickets_in_trouble",
                arguments={"created_last_hours": 24, "per_page": 25},
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
        flag_codes = [flag["code"] for flag in structured["tickets"][0]["flags"]]
        self.assertIn("meeting_summary_missing", flag_codes)
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_accepts_public_meeting_summary_from_assignee(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 914, "subject": "ACME | iOS | Login issue", "status": "open", "priority": "normal"},
            ],
            "count": 1,
            "page": 1,
            "per_page": 25,
            "sort_by": "created_at",
            "sort_order": "desc",
            "filters": {
                "created_last_hours": 24,
                "exclude_internal": True,
            },
            "has_more": False,
            "next_page": None,
            "previous_page": None,
        }
        full_ticket_payload = {
            "id": 914,
            "subject": "ACME | iOS | Login issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-10T16:00:00Z",
            "requester_id": 1001,
            "assignee_id": 2002,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Let's schedule a call on 2026-03-10 at 14:30.",
                "html_body": "<p>Let's schedule a call on 2026-03-10 at 14:30.</p>",
                "created_at": "2026-03-05T11:20:00Z",
                "attachments": [],
            },
            {
                "author_id": 2002,
                "public": True,
                "body": "Call summary: on the call we reviewed the iOS logs and agreed next steps.",
                "html_body": "<p>Call summary: on the call we reviewed the iOS logs and agreed next steps.</p>",
                "created_at": "2026-03-10T15:15:00Z",
                "attachments": [],
            },
        ]

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="scan_tickets_in_trouble",
                arguments={"created_last_hours": 24, "per_page": 25},
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
        flag_codes = [flag["code"] for flag in structured["tickets"][0]["flags"]]
        self.assertNotIn("meeting_summary_missing", flag_codes)
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_does_not_flag_customer_comment_before_4h_deadline(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 901, "subject": "ACME | iOS | Login issue", "status": "open", "priority": "normal"},
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
            "id": 901,
            "subject": "ACME | iOS | Login issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T11:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Looking into this.",
                "html_body": "<p>Looking into this.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "Any update?",
                "html_body": "<p>Any update?</p>",
                "created_at": "2026-03-05T11:00:00Z",
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
        flag_codes = [flag["code"] for flag in structured["tickets"][0]["flags"]]
        self.assertNotIn("customer_comment_no_response", flag_codes)
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_flags_customer_comment_no_response_for_low_priority_after_4h(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 902, "subject": "ACME | iOS | Login issue", "status": "open", "priority": "low"},
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
            "id": 902,
            "subject": "ACME | iOS | Login issue",
            "status": "open",
            "priority": "low",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T16:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Looking into this.",
                "html_body": "<p>Looking into this.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "Any update?",
                "html_body": "<p>Any update?</p>",
                "created_at": "2026-03-05T11:00:00Z",
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
        flag_codes = [flag["code"] for flag in structured["tickets"][0]["flags"]]
        self.assertIn("customer_comment_no_response", flag_codes)
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_ignores_no_response_expected_customer_comment(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 903, "subject": "ACME | iOS | Login issue", "status": "open", "priority": "normal"},
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
            "id": 903,
            "subject": "ACME | iOS | Login issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-07T10:00:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Please confirm once resolved.",
                "html_body": "<p>Please confirm once resolved.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "Thank you, you can close the ticket.",
                "html_body": "<p>Thank you, you can close the ticket.</p>",
                "created_at": "2026-03-05T11:00:00Z",
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
        flag_codes = [flag["code"] for flag in structured["tickets"][0]["flags"]]
        self.assertNotIn("customer_comment_no_response", flag_codes)
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_flags_no_response_expected_comment_when_open_5_days_without_updates(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 904, "subject": "ACME | iOS | Login issue", "status": "open", "priority": "normal"},
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
            "id": 904,
            "subject": "ACME | iOS | Login issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-11T12:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Please confirm once resolved.",
                "html_body": "<p>Please confirm once resolved.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "Thank you, you can close the ticket.",
                "html_body": "<p>Thank you, you can close the ticket.</p>",
                "created_at": "2026-03-05T11:00:00Z",
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
        flag_codes = [flag["code"] for flag in structured["tickets"][0]["flags"]]
        self.assertIn("customer_comment_no_response", flag_codes)
        self.assertFalse(response.root.isError)

    def test_non_escalated_support_owned_stale_ticket_is_flagged_without_using_priority(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 905,
            "subject": "ACME | iOS | Login issue",
            "status": "open",
            "priority": "low",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T19:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "Support Engineer",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
            "stale_age_hours": 9,
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Investigating this now.",
                "html_body": "<p>Investigating this now.</p>",
                "created_at": "2026-03-05T10:15:00Z",
                "attachments": [],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("support_owned_no_recent_updates", flag_codes)
        self.assertFalse(assessment.is_escalated)
        self.assertEqual(
            assessment.priority_interpretation,
            "Non-escalated ticket: Zendesk priority is not treated as severity; use flags and risk score.",
        )

    def test_escalated_high_priority_stale_ticket_keeps_eng_priority_signal(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 906,
            "subject": "ACME | iOS | Crash on launch",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T19:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "Support Engineer",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
                "Escalation Status": "Eng Escalated",
            },
            "stale_age_hours": 9,
        }

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=[],
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("high_priority_no_recent_updates", flag_codes)
        self.assertTrue(assessment.is_escalated)
        self.assertEqual(
            assessment.priority_interpretation,
            "Escalated ticket: Zendesk priority mirrors ENG priority.",
        )

    def test_internal_first_comment_skips_initial_response_flags(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 907,
            "subject": "ACME | iOS | Login issue",
            "status": "new",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T12:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "intake",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": False,
                "body": "Created by Appdome staff for follow-up.",
                "html_body": "<p>Created by Appdome staff for follow-up.</p>",
                "created_at": "2026-03-05T10:00:00Z",
                "attachments": [],
            },
            {
                "author_id": 2002,
                "public": True,
                "body": "We are investigating.",
                "html_body": "<p>We are investigating.</p>",
                "created_at": "2026-03-05T12:10:00Z",
                "attachments": [],
            },
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertNotIn("missing_initial_response", flag_codes)
        self.assertNotIn("late_initial_response", flag_codes)

    def test_ticket_with_live_appstore_end_user_impact_is_flagged_as_production_issue(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9900,
            "subject": "ACME | iOS | App Store app crashes at login",
            "description": "The app is already live and end users are impacted in production.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:20:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "Production",
            },
        }
        comments = [
            {
                "author_id": 1001,
                "public": True,
                "body": "This is live on the App Store and impacting customers now.",
                "html_body": "<p>This is live on the App Store and impacting customers now.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("production_user_impact", flag_codes)
        self.assertTrue(assessment.production_impact.is_production_issue)
        self.assertGreaterEqual(assessment.risk_score, 35)
        self.assertTrue(any("live store release" in evidence.lower() for evidence in assessment.production_impact.evidence))

    def test_internal_comment_indicating_customer_unhappy_is_flagged_high_priority(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9909,
            "subject": "ACME | iOS | Login issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:20:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": False,
                "body": "Customer is very frustrated and not happy with the delays.",
                "html_body": "<p>Customer is very frustrated and not happy with the delays.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("customer_unhappy", flag_codes)
        self.assertTrue(any("high-priority item" in flag.message for flag in assessment.flags))

    def test_customer_public_comment_indicating_unhappiness_is_flagged_high_priority(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9910,
            "subject": "ACME | Android | SDK issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:20:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 1001,
                "public": True,
                "body": "We are very disappointed. This delay is unacceptable.",
                "html_body": "<p>We are very disappointed. This delay is unacceptable.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("customer_unhappy", flag_codes)
        self.assertTrue(any("customer_public_comment" in flag.message for flag in assessment.flags))

    def test_ticket_with_uat_only_signals_is_not_flagged_as_production_issue(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9900,
            "subject": "ACME | iOS | UAT login issue",
            "description": "Issue reproduced only in UAT during testing.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:20:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "Testing / Pre-Release UAT",
            },
        }
        comments = [
            {
                "author_id": 1001,
                "public": True,
                "body": "Only seen in TestFlight and QA so far.",
                "html_body": "<p>Only seen in TestFlight and QA so far.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertNotIn("production_user_impact", flag_codes)
        self.assertFalse(assessment.production_impact.is_production_issue)
        self.assertTrue(assessment.production_impact.non_production_signals)

    def test_crash_ticket_attachment_filename_keyword_counts_as_stacktrace_evidence(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9901,
            "subject": "ACME | iOS | Crash after launch",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": ["crash_detected"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Thanks, we are investigating.",
                "html_body": "<p>Thanks, we are investigating.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [{"file_name": "ios_stack_capture.bin"}],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertNotIn("crash_process_gap", flag_codes)
        self.assertIsNotNone(assessment.crash_attachment_summary)
        self.assertTrue(assessment.crash_attachment_summary.has_crash_related_attachments)
        self.assertTrue(assessment.crash_attachment_summary.has_stacktrace_attachment)
        self.assertIn("ios_stack_capture.bin", assessment.crash_attachment_summary.stacktrace_files)

    def test_ticket_with_crash_attachment_evidence_missing_crash_tag_scores_100(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9906,
            "subject": "ACME | Android | Startup issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": ["mobile"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 1001,
                "public": True,
                "body": "Please see attached",
                "html_body": "<p>Please see attached</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [
                    {"file_name": "crash_1.jpg"},
                    {"file_name": "crash_2.jpg"},
                    {"file_name": "crash_3.jpg"},
                ],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("crash_tag_missing_unreviewed_attachment_evidence", flag_codes)
        self.assertEqual(assessment.risk_score, 100)

    def test_ticket_with_crash_attachment_evidence_and_crash_reviewed_tag_does_not_flag_missing_crash_tag(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9907,
            "subject": "ACME | Android | Startup issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": ["mobile", "crash_reviewed"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 1001,
                "public": True,
                "body": "Please see attached",
                "html_body": "<p>Please see attached</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [{"file_name": "crash_1.jpg"}],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertNotIn("crash_tag_missing_unreviewed_attachment_evidence", flag_codes)
        self.assertNotIn("crash_tag_missing", flag_codes)

    def test_ticket_with_crash_subject_and_missing_crash_tag_is_high_alert(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9905,
            "subject": "ACME | iOS | App crashing on launch",
            "description": "Customer reports repeat app crashing.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": ["mobile"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Thanks, we are checking this.",
                "html_body": "<p>Thanks, we are checking this.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("crash_tag_missing", flag_codes)

    def test_ticket_with_crash_description_and_missing_crash_tag_is_high_alert(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9906,
            "subject": "ACME | Android | Login issue",
            "description": "App closes unexpectedly and crashes when user signs in.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Thanks, we are checking this.",
                "html_body": "<p>Thanks, we are checking this.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("crash_tag_missing", flag_codes)

    def test_get_ticket_summary_includes_crash_tag_missing_alert(self) -> None:
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="get_ticket_summary",
                arguments={"ticket_id": 9910},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket_payload = {
            "id": 9910,
            "subject": "ACME | iOS | App crash when opening camera",
            "description": "Camera flow causes app crash.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:10:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
            "ticket_url": "https://appdomesupport.zendesk.com/agent/tickets/9910",
            "ticket_link": "[9910](https://appdomesupport.zendesk.com/agent/tickets/9910)",
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Investigating now.",
                "html_body": "<p>Investigating now.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [],
            }
        ]

        with (
            patch.object(server_module, "_prepare_ticket_payload", return_value=ticket_payload),
            patch.object(server_module, "zendesk_client") as mock_client,
        ):
            mock_client.get_ticket_comments.return_value = comments_payload
            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        summary_text = response.root.content[0].text
        self.assertIn("## Trouble Scan", summary_text)
        self.assertIn("crash_tag_missing", summary_text)
        self.assertIn("Crash-related attachments available:", summary_text)
        self.assertFalse(response.root.isError)

    def test_get_ticket_summary_explains_non_escalated_priority_handling(self) -> None:
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="get_ticket_summary",
                arguments={"ticket_id": 9911},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket_payload = {
            "id": 9911,
            "subject": "ACME | iOS | Login issue",
            "description": "Customer cannot log in.",
            "status": "open",
            "priority": "low",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T19:10:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "Support Engineer",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
            "stale_age_hours": 9,
            "ticket_url": "https://appdomesupport.zendesk.com/agent/tickets/9911",
            "ticket_link": "[9911](https://appdomesupport.zendesk.com/agent/tickets/9911)",
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Investigating now.",
                "html_body": "<p>Investigating now.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [],
            }
        ]

        with (
            patch.object(server_module, "_prepare_ticket_payload", return_value=ticket_payload),
            patch.object(server_module, "zendesk_client") as mock_client,
        ):
            mock_client.get_ticket_comments.return_value = comments_payload
            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        summary_text = response.root.content[0].text
        self.assertIn("Escalated: No", summary_text)
        self.assertIn(
            "Priority Interpretation: Non-escalated ticket: Zendesk priority is not treated as severity; use flags and risk score.",
            summary_text,
        )
        self.assertIn("support_owned_no_recent_updates", summary_text)
        self.assertFalse(response.root.isError)

    def test_get_ticket_summary_includes_production_issue_signal(self) -> None:
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="get_ticket_summary",
                arguments={"ticket_id": 9912},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket_payload = {
            "id": 9912,
            "subject": "ACME | Android | Play Store users blocked at enrollment",
            "description": "Production users are affected.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:10:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "Production",
            },
            "ticket_url": "https://appdomesupport.zendesk.com/agent/tickets/9912",
            "ticket_link": "[9912](https://appdomesupport.zendesk.com/agent/tickets/9912)",
        }
        comments_payload = [
            {
                "author_id": 1001,
                "public": True,
                "body": "This is live in Google Play and end users are impacted.",
                "html_body": "<p>This is live in Google Play and end users are impacted.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [],
            }
        ]

        with (
            patch.object(server_module, "_prepare_ticket_payload", return_value=ticket_payload),
            patch.object(server_module, "zendesk_client") as mock_client,
        ):
            mock_client.get_ticket_comments.return_value = comments_payload
            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        summary_text = response.root.content[0].text
        self.assertIn("Production Issue: Yes", summary_text)
        self.assertIn("Production Evidence:", summary_text)
        self.assertIn("production_user_impact", summary_text)
        self.assertFalse(response.root.isError)

    def test_get_ticket_summary_main_table_highlights_production_issue(self) -> None:
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="get_ticket_summary",
                arguments={"ticket_id": 9913},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket_payload = {
            "id": 9913,
            "subject": "ACME | iOS | Production login failures",
            "description": "Users are blocked in production.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:10:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "Production",
            },
            "ticket_url": "https://appdomesupport.zendesk.com/agent/tickets/9913",
            "ticket_link": "[9913](https://appdomesupport.zendesk.com/agent/tickets/9913)",
        }

        with (
            patch.object(server_module, "_prepare_ticket_payload", return_value=ticket_payload),
            patch.object(server_module, "zendesk_client") as mock_client,
        ):
            mock_client.get_ticket_comments.return_value = []
            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        summary_text = response.root.content[0].text
        self.assertIn("| Production Issue | Yes |", summary_text)
        self.assertFalse(response.root.isError)

    def test_review_ticket_resolves_merged_reference_from_last_comment(self) -> None:
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="review_ticket",
                arguments={"ticket_id": 9920},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        solved_ticket_payload = {
            "id": 9920,
            "subject": "Citi | Android | Old duplicate ticket",
            "status": "solved",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T12:00:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "resolved",
                "Release Stage": "n/a",
            },
        }
        solved_comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": 'This request was closed and merged into request #123456 "Citi | Android | Citi Authenticator".',
                "html_body": '<p>This request was closed and merged into request #123456 "Citi | Android | Citi Authenticator".</p>',
                "created_at": "2026-03-05T12:00:00Z",
                "attachments": [],
            }
        ]
        referenced_ticket_payload = {
            "id": 123456,
            "subject": "Citi | Android | Citi Authenticator",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T11:00:00Z",
            "updated_at": "2026-03-05T12:10:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        referenced_comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Continuing investigation on the merged ticket.",
                "html_body": "<p>Continuing investigation on the merged ticket.</p>",
                "created_at": "2026-03-05T12:05:00Z",
                "attachments": [],
            }
        ]

        with (
            patch.object(
                server_module,
                "_prepare_ticket_payload",
                side_effect=lambda tid: solved_ticket_payload if tid == 9920 else referenced_ticket_payload,
            ) as mock_prepare_ticket_payload,
            patch.object(server_module, "zendesk_client") as mock_client,
        ):
            mock_client.get_ticket_comments.side_effect = (
                lambda tid: solved_comments_payload if tid == 9920 else referenced_comments_payload
            )

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        payload = json.loads(response.root.content[0].text.split("Use the following evidence only.\n\n", 1)[1])
        self.assertEqual(payload["ticket_id"], 123456)
        self.assertEqual(payload["ticket"]["id"], 123456)
        self.assertIn("production_impact", payload["ticket"])
        self.assertFalse(payload["ticket"]["production_impact"]["is_production_issue"])
        self.assertEqual(len(payload["comments"]), 1)
        self.assertEqual(payload["comments"][0]["author_id"], 2002)
        self.assertEqual(payload["comments"][0]["body"], "Continuing investigation on the merged ticket.")
        self.assertTrue(payload["comments"][0]["public"])
        self.assertEqual(mock_prepare_ticket_payload.call_count, 2)
        self.assertFalse(response.root.isError)

    def test_crash_ticket_non_matching_attachment_filename_still_flags_process_gap(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9902,
            "subject": "ACME | Android | Crash after login",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": ["crash_detected"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Thanks, we are investigating.",
                "html_body": "<p>Thanks, we are investigating.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [{"file_name": "screen_recording.mp4"}],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("crash_process_gap", flag_codes)
        self.assertIsNotNone(assessment.crash_attachment_summary)
        self.assertTrue(assessment.crash_attachment_summary.has_replication_video)
        self.assertIn("screen_recording.mp4", assessment.crash_attachment_summary.replication_videos)

    def test_crash_ticket_generic_image_attachment_does_not_count_as_crash_evidence(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9903,
            "subject": "ACME | Android | Crash after login",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": ["crash_detected"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Thanks, we are investigating.",
                "html_body": "<p>Thanks, we are investigating.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [{"file_name": "screenshot.png"}],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("crash_process_gap", flag_codes)
        self.assertIsNotNone(assessment.crash_attachment_summary)
        self.assertFalse(assessment.crash_attachment_summary.has_crash_related_attachments)

    def test_crash_ticket_generic_log_attachment_does_not_count_as_crash_evidence(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9904,
            "subject": "ACME | Android | Crash after login",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": ["crash_detected"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Thanks, we are investigating.",
                "html_body": "<p>Thanks, we are investigating.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [{"file_name": "application.log"}],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("crash_process_gap", flag_codes)
        self.assertIsNotNone(assessment.crash_attachment_summary)
        self.assertFalse(assessment.crash_attachment_summary.has_crash_related_attachments)

    def test_crash_ticket_generic_zip_attachment_does_not_count_as_crash_evidence(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9908,
            "subject": "ACME | Android | Crash after login",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": ["crash_detected"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Thanks, we are investigating.",
                "html_body": "<p>Thanks, we are investigating.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [{"file_name": "logs.zip"}],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("crash_process_gap", flag_codes)
        self.assertIsNotNone(assessment.crash_attachment_summary)
        self.assertFalse(assessment.crash_attachment_summary.has_crash_related_attachments)

    def test_crash_ticket_log_attachment_with_crash_filename_counts_as_crash_evidence(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9909,
            "subject": "ACME | Android | Crash after login",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": ["crash_detected"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Attached requested crash log.",
                "html_body": "<p>Attached requested crash log.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [{"file_name": "android_crash.log"}],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertNotIn("crash_process_gap", flag_codes)
        self.assertIsNotNone(assessment.crash_attachment_summary)
        self.assertTrue(assessment.crash_attachment_summary.has_crash_related_attachments)
        self.assertIn("android_crash.log", assessment.crash_attachment_summary.crash_related_files)

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
            resolve_agent_id=True,
        )
        self.assertEqual([ticket["id"] for ticket in structured["tickets"]], expected_ids)
        self.assertEqual(structured["sampled_count"], 2)
        self.assertEqual(structured["total_matches"], 3)
        self.assertFalse(structured["truncated"])
        self.assertTrue(structured["exclude_api_created"])
        self.assertEqual(structured["excluded_api_created_count"], 0)
        self.assertFalse(response.root.isError)

    def test_review_random_solved_tickets_for_agent_highlights_production_tickets(self) -> None:
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="review_random_solved_tickets_for_agent",
                arguments={
                    "agent": "pedro",
                    "solved_after": "2026-02-01",
                    "solved_before": "2026-03-01",
                    "count": 2,
                    "seed": 3,
                    "max_pool": 50,
                },
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        sample_payload = server_module.RandomTicketSampleResult.model_validate(
            {
                "tickets": [
                    {
                        "id": 401,
                        "ticket_url": "https://appdomesupport.zendesk.com/agent/tickets/401",
                        "ticket_link": "[401](https://appdomesupport.zendesk.com/agent/tickets/401)",
                        "subject": "Prod ticket",
                        "status": "solved",
                        "priority": "high",
                    },
                    {
                        "id": 402,
                        "ticket_url": "https://appdomesupport.zendesk.com/agent/tickets/402",
                        "ticket_link": "[402](https://appdomesupport.zendesk.com/agent/tickets/402)",
                        "subject": "UAT ticket",
                        "status": "solved",
                        "priority": "normal",
                    },
                ],
                "requested_count": 2,
                "sampled_count": 2,
                "total_matches": 2,
                "retrieved_count": 2,
                "truncated": False,
                "exclude_api_created": False,
                "excluded_api_created_count": 0,
                "agent": "pedro",
                "solved_after": "2026-02-01",
                "solved_before": "2026-03-01",
                "seed": 3,
            }
        )

        prod_ticket = {
            "id": 401,
            "subject": "ACME | iOS | App Store outage",
            "description": "Production users are impacted.",
            "status": "solved",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T11:00:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "resolved",
                "Release Stage": "Production",
            },
            "ticket_link": "[401](https://appdomesupport.zendesk.com/agent/tickets/401)",
        }
        uat_ticket = {
            "id": 402,
            "subject": "ACME | iOS | UAT validation issue",
            "description": "Seen only in UAT testing.",
            "status": "solved",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T11:00:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "resolved",
                "Release Stage": "Testing / Pre-Release UAT",
            },
            "ticket_link": "[402](https://appdomesupport.zendesk.com/agent/tickets/402)",
        }

        with (
            patch.object(server_module, "sample_solved_tickets_for_agent", return_value=sample_payload),
            patch.object(
                server_module,
                "_prepare_ticket_payload",
                side_effect=lambda tid: prod_ticket if tid == 401 else uat_ticket,
            ),
            patch.object(server_module, "zendesk_client") as mock_client,
        ):
            mock_client.get_ticket_comments.return_value = []
            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual(structured["production_ticket_ids"], [401])
        self.assertEqual(structured["production_ticket_count"], 1)
        self.assertIn("[401](https://appdomesupport.zendesk.com/agent/tickets/401)", structured["production_ticket_links"])
        review_packet = json.loads(structured["review_input"].split("\n\n", 1)[1])
        self.assertTrue(review_packet["reviews"][0]["production_issue"])
        self.assertFalse(review_packet["reviews"][1]["production_issue"])
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

    def test_get_user_tool_returns_user_profile(self) -> None:
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="get_user",
                arguments={"user_id": 29115982058781},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with patch.object(server_module, "zendesk_client") as mock_client:
            mock_client.get_user.return_value = {
                "id": 29115982058781,
                "name": "Jane Doe",
                "email": "jane@example.com",
                "active": True,
                "role": "end-user",
                "organization_id": 123,
                "external_id": None,
            }
            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        payload = json.loads(response.root.content[0].text)
        self.assertEqual(payload["id"], 29115982058781)
        self.assertEqual(payload["name"], "Jane Doe")
        self.assertEqual(payload["email"], "jane@example.com")
        self.assertFalse(response.root.isError)

    def test_translate_user_ids_tool_returns_profiles_and_missing_ids(self) -> None:
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="translate_user_ids",
                arguments={"user_ids": [1001, 1002]},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with patch.object(server_module, "zendesk_client") as mock_client:
            mock_client.get_users_by_ids.return_value = {
                1001: {
                    "id": 1001,
                    "name": "Requester One",
                    "email": "requester1@example.com",
                    "active": True,
                    "role": "end-user",
                    "organization_id": None,
                    "external_id": None,
                }
            }
            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual(structured["users_by_id"]["1001"]["email"], "requester1@example.com")
        self.assertEqual(structured["missing_ids"], [1002])
        self.assertFalse(response.root.isError)


if __name__ == "__main__":
    unittest.main()
