import asyncio
import importlib
import json
import os
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
        self.assertEqual(result["tickets"][0]["match_type"], "exact")
        self.assertEqual(result["exact_query"], query)
        self.assertFalse(result["partial_fallback_used"])
        self.assertEqual(result["search_mode"], "exact")

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

    def test_search_tickets_by_text_uses_partial_fallback_after_zero_exact_matches(self) -> None:
        exact_payload = {"results": [], "next_page": None}
        partial_payload = {
            "results": [
                {
                    "result_type": "ticket",
                    "id": 777,
                    "subject": "Facephi SDK issue",
                    "status": "open",
                    "priority": "normal",
                    "created_at": "2026-03-01T10:00:00Z",
                    "updated_at": "2026-03-02T10:00:00Z",
                }
            ],
            "next_page": None,
        }

        mock_response_one = MagicMock()
        mock_response_one.read.return_value = json.dumps(exact_payload).encode("utf-8")
        mock_response_two = MagicMock()
        mock_response_two.read.return_value = json.dumps(partial_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.side_effect = [mock_response_one, mock_response_two]

        with patch("zendesk_mcp_server.zendesk_client.urllib.request.urlopen", mock_urlopen):
            result = self.client.search_tickets_by_text(phrase="Facephi SDK")

        first_request = mock_urlopen.call_args_list[0].args[0]
        first_query = unquote(parse_qs(urlparse(first_request.full_url).query)["query"][0])
        second_request = mock_urlopen.call_args_list[1].args[0]
        second_query = unquote(parse_qs(urlparse(second_request.full_url).query)["query"][0])

        self.assertEqual(first_query, 'type:ticket "Facephi SDK" status<solved')
        self.assertEqual(second_query, "type:ticket Facephi status<solved")
        self.assertEqual(result["tickets"][0]["match_type"], "partial")
        self.assertEqual(result["search_mode"], "partial_fallback")
        self.assertEqual(result["exact_count"], 0)
        self.assertTrue(result["partial_fallback_used"])
        self.assertEqual(result["partial_query"], second_query)
        self.assertIsNone(result["partial_fallback_reason"])

    def test_search_tickets_by_text_skips_partial_fallback_for_common_short_phrase(self) -> None:
        api_payload = {"results": [], "next_page": None}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with patch("zendesk_mcp_server.zendesk_client.urllib.request.urlopen", mock_urlopen):
            result = self.client.search_tickets_by_text(phrase="to")

        self.assertEqual(len(mock_urlopen.call_args_list), 1)
        self.assertEqual(result["search_mode"], "exact_no_partial_fallback")
        self.assertFalse(result["partial_fallback_used"])
        self.assertEqual(result["partial_query"], None)
        self.assertEqual(result["partial_fallback_reason"], "phrase is too short or too common for safe partial fallback")


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

    def test_get_important_tickets_today_emits_structured_content(self) -> None:
        recent_payload = {
            "tickets": [
                {"id": 101, "subject": "ACME | Android | Login issue", "status": "open", "priority": "normal"},
                {"id": 102, "subject": "ACME | iOS | Release blocked", "status": "open", "priority": "high"},
            ],
            "count": 2,
            "page": 1,
            "per_page": 25,
            "sort_by": "updated_at",
            "sort_order": "desc",
            "filters": {"last_hours": 24, "exclude_internal": True},
            "has_more": False,
            "next_page": None,
            "previous_page": None,
        }
        stale_payload = {
            "tickets": [
                {"id": 102, "subject": "ACME | iOS | Release blocked", "status": "open", "priority": "high"},
            ],
            "count": 1,
            "page": 1,
            "per_page": 25,
            "sort_by": "updated_at",
            "sort_order": "asc",
            "filters": {"stale_hours": 8, "exclude_internal": True},
            "has_more": False,
            "next_page": None,
            "previous_page": None,
        }
        full_ticket_by_id = {
            101: {
                "id": 101,
                "subject": "ACME | Android | Login issue",
                "status": "open",
                "priority": "normal",
                "created_at": "2026-03-05T10:00:00Z",
                "updated_at": "2026-03-05T12:00:00Z",
                "requester_id": 1001,
                "tags": [],
                "custom_fields": {
                    "Status With": "Support Engineer",
                    "Support Stage": "investigation",
                    "Release Stage": "UAT",
                },
            },
            102: {
                "id": 102,
                "subject": "ACME | iOS | Release blocked",
                "status": "open",
                "priority": "high",
                "created_at": "2026-03-05T10:00:00Z",
                "updated_at": "2026-03-05T20:00:00Z",
                "requester_id": 1002,
                "tags": [],
                "custom_fields": {
                    "Status With": "Support Engineer",
                    "Support Stage": "investigation",
                    "Release Stage": "PROD",
                },
                "stale_age_hours": 10,
            },
        }
        comments_by_id = {
            101: [
                {
                    "author_id": 2002,
                    "public": True,
                    "body": "Investigating.",
                    "html_body": "<p>Investigating.</p>",
                    "created_at": "2026-03-05T10:15:00Z",
                    "attachments": [],
                }
            ],
            102: [],
        }
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="get_important_tickets_today",
                arguments={"recent_activity_hours": 24, "stale_hours": 8, "per_page": 25, "exclude_internal": True},
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
            mock_client.get_tickets.side_effect = [recent_payload, stale_payload]
            mock_client.get_ticket_comments.side_effect = lambda ticket_id: comments_by_id[ticket_id]

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual(structured["filters"]["recent_activity_hours"], 24)
        self.assertEqual(structured["filters"]["stale_hours"], 8)
        self.assertEqual(structured["candidate_count"], 2)
        self.assertEqual(structured["in_trouble_count"], 1)
        self.assertEqual([ticket["ticket_id"] for ticket in structured["tickets"]], [102, 101])
        self.assertIn("support_owned_no_recent_updates", [flag["code"] for flag in structured["tickets"][0]["flags"]])
        self.assertEqual(mock_client.get_tickets.call_count, 2)
        mock_client.get_tickets.assert_any_call(
            page=1,
            per_page=25,
            sort_by="updated_at",
            sort_order="desc",
            agent=None,
            organization=None,
            last_hours=24,
            exclude_internal=True,
        )
        mock_client.get_tickets.assert_any_call(
            page=1,
            per_page=25,
            sort_by="updated_at",
            sort_order="asc",
            agent=None,
            organization=None,
            stale_hours=8,
            exclude_internal=True,
        )
        self.assertFalse(response.root.isError)

    def test_get_important_tickets_today_deduplicates_candidates(self) -> None:
        shared_ticket = {"id": 101, "subject": "ACME | Android | Login issue", "status": "open", "priority": "normal"}
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="get_important_tickets_today",
                arguments={"recent_activity_hours": 24, "stale_hours": 8, "per_page": 25},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with (
            patch.object(server_module, "zendesk_client") as mock_client,
            patch.object(
                server_module,
                "_prepare_ticket_payload",
                return_value={
                    "id": 101,
                    "subject": "ACME | Android | Login issue",
                    "status": "open",
                    "priority": "normal",
                    "created_at": "2026-03-05T10:00:00Z",
                    "updated_at": "2026-03-05T10:20:00Z",
                    "requester_id": 1001,
                    "tags": [],
                    "custom_fields": {
                        "Status With": "customer",
                        "Support Stage": "investigation",
                        "Release Stage": "UAT",
                    },
                },
            ) as mock_prepare_ticket_payload,
        ):
            mock_client.get_tickets.side_effect = [
                {"tickets": [shared_ticket]},
                {"tickets": [shared_ticket]},
            ]
            mock_client.get_ticket_comments.return_value = []

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual(structured["candidate_count"], 1)
        mock_prepare_ticket_payload.assert_called_once_with(101)
        mock_client.get_ticket_comments.assert_called_once_with(101)
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

    def test_scan_tickets_in_trouble_ignores_feature_request_titles(self) -> None:
        list_payload = {
            "tickets": [
                {
                    "id": 780,
                    "subject": "Feature Request - Bulk export for dashboard",
                    "status": "open",
                    "priority": "high",
                },
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

    def test_scan_tickets_in_trouble_ignores_titles_containing_feature_request_words(self) -> None:
        list_payload = {
            "tickets": [
                {
                    "id": 781,
                    "subject": "ACME | Android | Feature Request for dashboard export",
                    "status": "open",
                    "priority": "high",
                },
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

    def test_pending_tickets_are_deprioritized_in_trouble_score(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        open_ticket = {
            "id": 920,
            "subject": "ACME | Android | Crash after login",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T20:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "PROD",
            },
        }
        pending_ticket = {
            **open_ticket,
            "id": 921,
            "status": "pending",
        }

        open_assessment = server_module._build_ticket_trouble_assessment(
            ticket=open_ticket,
            comments=[],
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )
        pending_assessment = server_module._build_ticket_trouble_assessment(
            ticket=pending_ticket,
            comments=[],
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        self.assertGreater(open_assessment.risk_score, pending_assessment.risk_score)
        self.assertIn("Pending ticket: lower priority by default", pending_assessment.priority_interpretation)

    def test_pending_ticket_with_engineering_jira_resolution_gets_pending_discount(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 922,
            "subject": "ACME | Android | Crash after login",
            "status": "pending",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T20:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "PROD",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": False,
                "body": (
                    "Engineering JIRA Update\n\n"
                    "Configuration changes:\n"
                    "--encrypt_string_exclude_list <encrypt_string_exclude_list.txt>\n\n"
                    "Update Description:\n"
                    "Engineering provided the required configuration changes and the workaround to apply.\n\n"
                    "Updated by:\n"
                    "Yoav Keissar"
                ),
                "html_body": "",
                "created_at": "2026-03-05T12:00:00Z",
                "attachments": [],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        self.assertEqual(assessment.risk_score, 20)
        self.assertIn("likely resolution", assessment.priority_interpretation)

    def test_pending_ticket_with_engineering_jira_status_update_keeps_higher_risk(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 923,
            "subject": "ACME | Android | Crash after login",
            "status": "pending",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T20:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "PROD",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": False,
                "body": (
                    "Engineering JIRA Update\n\n"
                    "Dev Reference:\n"
                    "Configuration changes:\n"
                    "--encrypt_string_exclude_list <encrypt_string_exclude_list.txt>\n"
                    "--assets_ignore_list <assets_ignore_list.txt>\n"
                    "--smali_renames_classes_extend_list <smali_renames_classes_extend_list.txt>\n\n"
                    "Update Description:\n"
                    "The FIDO SDK fingerprint authentication flow classifies errors by type and assigns a different "
                    "retry behavior to each. We are updating the configuration to better match the FIDO SDK error "
                    "handling, but we have not been able to validate it yet since we cannot reproduce the issue.\n\n"
                    "Updated by:\n"
                    "Yoav Keissar"
                ),
                "html_body": "",
                "created_at": "2026-03-05T12:00:00Z",
                "attachments": [],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        self.assertEqual(assessment.risk_score, 35)
        self.assertIn("do not lower risk by default", assessment.priority_interpretation)
        self.assertTrue(assessment.engineering_jira_update_summaries)
        self.assertIn("Engineering update (status, 2026-03-05T12:00:00Z)", assessment.engineering_jira_update_summaries[0])

    def test_engineering_jira_update_summaries_include_multiple_updates(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 924,
            "subject": "ACME | Android | Crash after login",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T20:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "PROD",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": False,
                "body": (
                    "Engineering JIRA Update\n\n"
                    "Update Description:\n"
                    "ETA is tomorrow after validation is complete.\n\n"
                    "Updated by:\n"
                    "Yoav Keissar"
                ),
                "html_body": "",
                "created_at": "2026-03-05T12:00:00Z",
                "attachments": [],
            },
            {
                "author_id": 2002,
                "public": False,
                "body": (
                    "Engineering JIRA Update\n\n"
                    "Update Description:\n"
                    "Configuration changes were identified, but the team has not been able to validate them yet.\n\n"
                    "Updated by:\n"
                    "Yoav Keissar"
                ),
                "html_body": "",
                "created_at": "2026-03-05T14:00:00Z",
                "attachments": [],
            },
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        self.assertEqual(len(assessment.engineering_jira_update_summaries), 2)
        self.assertIn("2026-03-05T14:00:00Z", assessment.engineering_jira_update_summaries[0])
        self.assertIn("2026-03-05T12:00:00Z", assessment.engineering_jira_update_summaries[1])

    def test_ticket_markdown_list_includes_engineering_update_summaries(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        assessment = server_module.TicketTroubleAssessment(
            ticket_id=99104,
            ticket_url="https://appdomesupport.zendesk.com/agent/tickets/99104",
            ticket_link="[99104](https://appdomesupport.zendesk.com/agent/tickets/99104)",
            subject="ACME | Android | SDK crash issue",
            status="pending",
            priority="normal",
            is_escalated=False,
            in_trouble=True,
            risk_score=42,
            flags=[],
            engineering_jira_update_summaries=[
                "Engineering update (status, 2026-03-05T14:00:00Z): Configuration changes were identified, but validation is still pending.",
                "Engineering update (eta, 2026-03-05T12:00:00Z): ETA is tomorrow after validation is complete.",
            ],
        )

        markdown = server_module._build_ticket_trouble_markdown_list([assessment])
        self.assertIn("Engineering: Engineering update (status, 2026-03-05T14:00:00Z)", markdown)
        self.assertIn("Engineering: Engineering update (eta, 2026-03-05T12:00:00Z)", markdown)

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

    def test_scan_tickets_in_trouble_markdown_highlights_customer_urgency(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 42468, "subject": "ACME | Android | Login issue", "status": "open", "priority": "normal"},
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
            "id": 42468,
            "subject": "ACME | Android | Login issue",
            "description": "This issue is urgent for the customer.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T12:30:00Z",
            "requester_id": 1002,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "PROD",
            },
        }
        comments = [
            {
                "author_id": 1002,
                "public": True,
                "body": "Please treat this as urgent.",
                "html_body": "<p>Please treat this as urgent.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [],
            },
            {
                "author_id": 2002,
                "public": True,
                "body": "We are looking into it.",
                "html_body": "<p>We are looking into it.</p>",
                "created_at": "2026-03-05T10:20:00Z",
                "attachments": [],
            },
        ]

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
            mock_client.get_ticket_comments.return_value = comments

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertIn("highlights=CUSTOMER-URGENT", structured["ticket_list_markdown"])
        self.assertEqual(structured["tickets"][0]["flags"][0]["code"], "customer_urgency")
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

    def test_scan_crash_tickets_in_trouble_scans_tagged_tickets_without_created_last_hours_window(self) -> None:
        search_payload = {
            "tickets": [
                {
                    "id": 9150,
                    "subject": "ACME | iOS | Crash on startup",
                    "status": "open",
                    "priority": "normal",
                    "created_at": "2026-03-01T10:00:00Z",
                    "updated_at": "2026-03-20T10:00:00Z",
                },
            ],
            "query": "type:ticket tags:crash_detected status:open -tags:internal",
            "total_matches": 1,
            "retrieved_count": 1,
            "truncated": False,
        }
        full_ticket_payload = {
            "id": 9150,
            "subject": "ACME | iOS | Crash on startup",
            "description": "Production users see a crash on startup.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-01T10:00:00Z",
            "updated_at": "2026-03-20T10:00:00Z",
            "requester_id": 1001,
            "tags": ["crash_detected"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "Production",
            },
        }
        comments_payload = [
            {
                "author_id": 1001,
                "public": True,
                "body": "This is still crashing in production and we are waiting for an update.",
                "html_body": "<p>This is still crashing in production and we are waiting for an update.</p>",
                "created_at": "2026-03-20T08:00:00Z",
                "attachments": [],
            },
        ]

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="scan_crash_tickets_in_trouble",
                arguments={"tag": "crash_detected", "max_results": 50},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with (
            patch.object(server_module, "zendesk_client") as mock_client,
            patch.object(server_module, "_prepare_ticket_payload", return_value=full_ticket_payload),
        ):
            mock_client.search_open_tickets_by_tag.return_value = search_payload
            mock_client.get_ticket_comments.return_value = comments_payload

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual(structured["tag"], "crash_detected")
        self.assertEqual(structured["scanned_count"], 1)
        self.assertEqual(structured["total_matches"], 1)
        self.assertEqual(structured["retrieved_count"], 1)
        self.assertFalse(structured["truncated"])
        self.assertEqual(structured["tickets"][0]["ticket_id"], 9150)
        self.assertIn("production_user_impact", [flag["code"] for flag in structured["tickets"][0]["flags"]])
        mock_client.search_open_tickets_by_tag.assert_called_once_with(
            tag="crash_detected",
            max_results=50,
            per_page=100,
            include_solved=False,
            exclude_internal=True,
        )
        self.assertFalse(response.root.isError)

    def test_scan_crash_tickets_in_trouble_skips_pending_crash_tickets(self) -> None:
        search_payload = {
            "tickets": [
                {
                    "id": 9151,
                    "subject": "ACME | iOS | Crash on startup",
                    "status": "pending",
                    "priority": "normal",
                    "created_at": "2026-03-01T10:00:00Z",
                    "updated_at": "2026-03-20T10:00:00Z",
                },
            ],
            "query": "type:ticket tags:crash_detected status:open -tags:internal",
            "total_matches": 1,
            "retrieved_count": 1,
            "truncated": False,
        }
        full_ticket_payload = {
            "id": 9151,
            "subject": "ACME | iOS | Crash on startup",
            "status": "pending",
            "priority": "normal",
            "created_at": "2026-03-01T10:00:00Z",
            "updated_at": "2026-03-20T10:00:00Z",
            "requester_id": 1001,
            "tags": ["crash_detected"],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "Production",
            },
        }

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="scan_crash_tickets_in_trouble",
                arguments={"tag": "crash_detected", "max_results": 50},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        with (
            patch.object(server_module, "zendesk_client") as mock_client,
            patch.object(server_module, "_prepare_ticket_payload", return_value=full_ticket_payload),
        ):
            mock_client.search_open_tickets_by_tag.return_value = search_payload

            handler = server_module.mcp._mcp_server.request_handlers[CallToolRequest]
            response = asyncio.run(handler(request))

        structured = response.root.structuredContent
        self.assertEqual(structured["scanned_count"], 0)
        self.assertEqual(structured["in_trouble_count"], 0)
        self.assertEqual(structured["tickets"], [])
        mock_client.get_ticket_comments.assert_not_called()
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

    def test_scan_tickets_in_trouble_accepts_private_meeting_summary_from_assignee(self) -> None:
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
                "public": False,
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

    def test_scan_tickets_in_trouble_does_not_treat_stacktrace_timestamp_as_meeting(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 915, "subject": "ACME | Android | Crash on launch", "status": "open", "priority": "high"},
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
            "id": 915,
            "subject": "ACME | Android | Crash on launch",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-10T22:00:00Z",
            "requester_id": 1001,
            "assignee_id": 2002,
            "tags": ["crash_detected"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "production",
            },
        }
        comments_payload = [
            {
                "author_id": 1001,
                "public": True,
                "body": (
                    "Crash log from device:\\n"
                    "2026-03-10 21:16:59 Fatal Exception: java.lang.IllegalStateException\\n"
                    "at com.example.ScheduledTaskRunner.run(ScheduledTaskRunner.java:42)"
                ),
                "html_body": (
                    "<p>Crash log from device:</p>"
                    "<p>2026-03-10 21:16:59 Fatal Exception: java.lang.IllegalStateException</p>"
                    "<p>at com.example.ScheduledTaskRunner.run(ScheduledTaskRunner.java:42)</p>"
                ),
                "created_at": "2026-03-10T21:20:00Z",
                "attachments": [],
            }
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

    def test_scan_tickets_in_trouble_does_not_treat_call_this_api_as_meeting(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 916, "subject": "ACME | Android | Crash on launch", "status": "open", "priority": "high"},
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
            "id": 916,
            "subject": "ACME | Android | Crash on launch",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-10T19:40:00Z",
            "updated_at": "2026-03-12T13:42:02Z",
            "requester_id": 1001,
            "assignee_id": 2002,
            "tags": ["crash_detected"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "production",
            },
        }
        comments_payload = [
            {
                "author_id": 1001,
                "public": True,
                "body": (
                    "java.lang.SecurityException: Need to declare android.permission.REQUEST_INSTALL_PACKAGES "
                    "to call this api\n"
                    "2026-03-10 21:16:59.082 23109-23187 System.err "
                    "com.example.app W at android.os.Parcel.createExceptionOrNull(Parcel.java:3361)"
                ),
                "html_body": (
                    "<p>java.lang.SecurityException: Need to declare "
                    "android.permission.REQUEST_INSTALL_PACKAGES to call this api</p>"
                    "<p>2026-03-10 21:16:59.082 23109-23187 System.err "
                    "com.example.app W at android.os.Parcel.createExceptionOrNull(Parcel.java:3361)</p>"
                ),
                "created_at": "2026-03-10T19:48:37Z",
                "attachments": [],
            },
            {
                "author_id": 2002,
                "public": True,
                "body": "I was able to reproduce the reported behavior and this case is now with engineering.",
                "html_body": "<p>I was able to reproduce the reported behavior and this case is now with engineering.</p>",
                "created_at": "2026-03-11T02:32:32Z",
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

    def test_scan_tickets_in_trouble_flags_unanswered_production_customer_comment_more_aggressively(self) -> None:
        list_payload = {
            "tickets": [
                {"id": 9021, "subject": "ACME | iOS | Production login issue", "status": "open", "priority": "normal"},
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
            "id": 9021,
            "subject": "ACME | iOS | Production login issue",
            "description": "Live production users are blocked from logging in.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T13:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "Production",
            },
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "We are investigating now.",
                "html_body": "<p>We are investigating now.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "This is impacting production users. We are still waiting for an Appdome response.",
                "html_body": "<p>This is impacting production users. We are still waiting for an Appdome response.</p>",
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
        ticket = structured["tickets"][0]
        flag_codes = [flag["code"] for flag in ticket["flags"]]
        self.assertIn("production_customer_comment_no_response", flag_codes)
        self.assertIn("PROD-NO-RESPONSE", structured["ticket_list_markdown"])
        self.assertGreaterEqual(ticket["risk_score"], 56)
        self.assertFalse(response.root.isError)

    def test_scan_tickets_in_trouble_accepts_internal_appdome_follow_up_after_customer_comment(self) -> None:
        list_payload = {
            "tickets": [
                {
                    "id": 902,
                    "subject": "ACME | Android | Crash on launch",
                    "status": "open",
                    "priority": "high",
                },
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
            "subject": "ACME | Android | Crash on launch",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-05T09:00:00Z",
            "updated_at": "2026-03-05T15:30:00Z",
            "requester_id": 1001,
            "tags": ["crash_detected", "prod_impact"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "production",
            },
        }
        comments_payload = [
            {
                "author_id": 2002,
                "public": True,
                "body": "We are investigating this crash.",
                "html_body": "<p>We are investigating this crash.</p>",
                "created_at": "2026-03-05T09:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "This is impacting production users. We are still waiting for an Appdome response.",
                "html_body": "<p>This is impacting production users. We are still waiting for an Appdome response.</p>",
                "created_at": "2026-03-05T11:00:00Z",
                "attachments": [],
            },
            {
                "author_id": 3003,
                "public": False,
                "body": "Got it. Engineering is looking at this now.",
                "html_body": "<p>Got it. Engineering is looking at this now.</p>",
                "created_at": "2026-03-05T11:15:00Z",
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
        ticket = structured["tickets"][0]
        flag_codes = [flag["code"] for flag in ticket["flags"]]
        self.assertNotIn("production_customer_comment_no_response", flag_codes)
        self.assertNotIn("customer_comment_no_response", flag_codes)
        self.assertNotIn("PROD-NO-RESPONSE", structured["ticket_list_markdown"])
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

    def test_open_ticket_with_customer_acknowledgement_is_flagged_for_closure_follow_up(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9041,
            "subject": "ACME | iOS | Login issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T11:00:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "Customer",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "We identified the issue and shared the analysis.",
                "html_body": "<p>We identified the issue and shared the analysis.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "Thanks for the analysis and identifying the issue.",
                "html_body": "<p>Thanks for the analysis and identifying the issue.</p>",
                "created_at": "2026-03-05T10:30:00Z",
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
        self.assertIn("customer_acknowledged_resolution_ticket_still_open", flag_codes)
        self.assertNotIn("customer_comment_no_response", flag_codes)

    def test_solved_ticket_acknowledgement_of_answered_concern_counts_as_customer_confirmation(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9042,
            "subject": "ACME | iOS | Login issue",
            "status": "solved",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T11:00:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "Customer",
                "Support Stage": "resolved",
                "Release Stage": "n/a",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "We identified the issue and shared the analysis.",
                "html_body": "<p>We identified the issue and shared the analysis.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "Thanks for the analysis and identifying the issue.",
                "html_body": "<p>Thanks for the analysis and identifying the issue.</p>",
                "created_at": "2026-03-05T10:30:00Z",
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
        self.assertNotIn("solved_without_customer_confirmation", flag_codes)

    def test_customer_acknowledgement_uses_llm_intent_classifier_when_configured(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        comment = {
            "author_id": 1001,
            "public": True,
            "body": "Thanks for the analysis and identifying the issue.",
            "html_body": "<p>Thanks for the analysis and identifying the issue.</p>",
            "created_at": "2026-03-05T10:30:00Z",
            "attachments": [],
        }
        response_payload = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"is_resolution_acknowledgement": true}',
                        }
                    ],
                }
            ]
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(response_payload).encode("utf-8")
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with (
            patch.dict(
                os.environ,
                {
                    "COMMENT_INTENT_CLASSIFIER_MODE": "llm",
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_COMMENT_CLASSIFIER_MODEL": "test-model",
                },
                clear=False,
            ),
            patch("zendesk_mcp_server.server.urllib.request.urlopen", mock_urlopen),
        ):
            server_module._classify_customer_comment_intent_with_llm.cache_clear()
            result = server_module._customer_comment_indicates_resolution_acknowledgement(comment)

        self.assertTrue(result)
        request = mock_urlopen.call_args.args[0]
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_payload["model"], "test-model")
        self.assertEqual(
            request_payload["text"]["format"]["name"],
            "customer_comment_intent",
        )

    def test_customer_acknowledgement_falls_back_to_heuristic_when_llm_unavailable(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        comment = {
            "author_id": 1001,
            "public": True,
            "body": "Thanks for the analysis and identifying the issue.",
            "html_body": "<p>Thanks for the analysis and identifying the issue.</p>",
            "created_at": "2026-03-05T10:30:00Z",
            "attachments": [],
        }

        with (
            patch.dict(
                os.environ,
                {
                    "COMMENT_INTENT_CLASSIFIER_MODE": "llm",
                    "OPENAI_API_KEY": "test-key",
                },
                clear=False,
            ),
            patch(
                "zendesk_mcp_server.server.urllib.request.urlopen",
                side_effect=OSError("network unavailable"),
            ),
        ):
            server_module._classify_customer_comment_intent_with_llm.cache_clear()
            result = server_module._customer_comment_indicates_resolution_acknowledgement(comment)

        self.assertTrue(result)

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

    def test_sev1_ticket_waiting_for_customer_data_is_flagged_after_one_hour(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9061,
            "subject": "ACME | Android | Crash on launch",
            "status": "open",
            "priority": "urgent",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T12:20:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "Customer",
                "Support Stage": "investigation",
                "Release Stage": "production",
                "Escalation Status": "Eng Escalated",
                "Priority": "SEV 1",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Please provide the crash logs and a screen recording so we can continue.",
                "html_body": "<p>Please provide the crash logs and a screen recording so we can continue.</p>",
                "created_at": "2026-03-05T10:45:00Z",
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
        self.assertIn("sev1_customer_data_follow_up_overdue", flag_codes)

    def test_sev1_ticket_without_requested_data_does_not_clear_on_customer_acknowledgement(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9062,
            "subject": "ACME | Android | Crash on launch",
            "status": "open",
            "priority": "urgent",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T13:30:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "Customer",
                "Support Stage": "investigation",
                "Release Stage": "production",
                "Escalation Status": "Eng Escalated",
                "Priority": "SEV 1",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Could you please share the device logs and exact app version?",
                "html_body": "<p>Could you please share the device logs and exact app version?</p>",
                "created_at": "2026-03-05T10:45:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "We are checking with the team and will send that later.",
                "html_body": "<p>We are checking with the team and will send that later.</p>",
                "created_at": "2026-03-05T11:50:00Z",
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
        self.assertIn("sev1_customer_data_follow_up_overdue", flag_codes)

    def test_sev1_ticket_clears_hourly_follow_up_once_customer_provides_requested_data(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9063,
            "subject": "ACME | Android | Crash on launch",
            "status": "open",
            "priority": "urgent",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T12:10:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "Customer",
                "Support Stage": "investigation",
                "Release Stage": "production",
                "Escalation Status": "Eng Escalated",
                "Priority": "SEV 1",
            },
        }
        comments = [
            {
                "author_id": 2002,
                "public": True,
                "body": "Please send the crash logs and steps to reproduce.",
                "html_body": "<p>Please send the crash logs and steps to reproduce.</p>",
                "created_at": "2026-03-05T10:45:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "Attached are the crash logs from the affected device.",
                "html_body": "<p>Attached are the crash logs from the affected device.</p>",
                "created_at": "2026-03-05T11:15:00Z",
                "attachments": [
                    {
                        "file_name": "device-crash.log",
                        "content_type": "text/plain",
                    }
                ],
            },
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertNotIn("sev1_customer_data_follow_up_overdue", flag_codes)

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

    def test_internal_tag_without_internal_in_title_is_flagged_as_system_mismatch(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 908,
            "subject": "ACME | iOS | Login issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": ["internal"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=[],
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertIn("internal_tag_title_mismatch", flag_codes)
        self.assertTrue(any("possible system tagging/title-sync issue" in flag.message for flag in assessment.flags))

    def test_internal_tag_with_internal_in_title_is_not_flagged(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 909,
            "subject": "Internal | ACME | iOS | Login issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:30:00Z",
            "requester_id": 1001,
            "tags": ["internal"],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "investigation",
                "Release Stage": "n/a",
            },
        }

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=[],
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        flag_codes = [flag.code for flag in assessment.flags]
        self.assertNotIn("internal_tag_title_mismatch", flag_codes)

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

    def test_customer_follow_up_comment_without_explicit_unhappy_words_is_still_flagged_from_comment_language(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 99101,
            "subject": "ACME | Android | SDK issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T16:20:00Z",
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
                "body": "Investigating.",
                "html_body": "<p>Investigating.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "Following up again. We are still waiting for a response.",
                "html_body": "<p>Following up again. We are still waiting for a response.</p>",
                "created_at": "2026-03-05T11:00:00Z",
                "attachments": [],
            },
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        customer_unhappy_flags = [flag for flag in assessment.flags if flag.code == "customer_unhappy"]
        self.assertTrue(customer_unhappy_flags)
        self.assertIn("still waiting", customer_unhappy_flags[0].message.lower())
        self.assertTrue(any("Recent customer comment" in note for note in assessment.recent_comment_notes))

    def test_multiple_customer_pressure_comments_are_marked_specifically(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 99102,
            "subject": "ACME | Android | SDK crash issue",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T17:20:00Z",
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
                "author_id": 2002,
                "public": True,
                "body": "Investigating.",
                "html_body": "<p>Investigating.</p>",
                "created_at": "2026-03-05T10:10:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "We are frustrated and need regular updates on this production crash.",
                "html_body": "<p>We are frustrated and need regular updates on this production crash.</p>",
                "created_at": "2026-03-05T11:00:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "Please schedule a Zoom meeting and keep us updated hourly.",
                "html_body": "<p>Please schedule a Zoom meeting and keep us updated hourly.</p>",
                "created_at": "2026-03-05T13:00:00Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "Following up again. We are still waiting for a response.",
                "html_body": "<p>Following up again. We are still waiting for a response.</p>",
                "created_at": "2026-03-05T16:00:00Z",
                "attachments": [],
            },
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        pressure_flags = [flag for flag in assessment.flags if flag.code == "customer_repeated_pressure"]
        self.assertTrue(pressure_flags)
        self.assertIn("multiple pressure/escalation comments", pressure_flags[0].message.lower())
        self.assertIn("repeat update requests", pressure_flags[0].message.lower())
        self.assertIn("meeting/call requests", pressure_flags[0].message.lower())
        self.assertIn("dissatisfaction/frustration", pressure_flags[0].message.lower())

    def test_scan_tickets_in_trouble_markdown_highlights_customer_pressure(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        assessment = server_module.TicketTroubleAssessment(
            ticket_id=99103,
            ticket_url="https://appdomesupport.zendesk.com/agent/tickets/99103",
            ticket_link="[99103](https://appdomesupport.zendesk.com/agent/tickets/99103)",
            subject="ACME | Android | SDK crash issue",
            status="open",
            priority="normal",
            is_escalated=False,
            in_trouble=True,
            risk_score=88,
            flags=[
                server_module.TicketTroubleFlag(
                    code="customer_repeated_pressure",
                    severity="high",
                    message="Customer posted multiple pressure/escalation comments.",
                )
            ],
        )

        markdown = server_module._build_ticket_trouble_markdown_list([assessment])
        self.assertIn("CUSTOMER-PRESSURE", markdown)

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

    def test_training_request_in_prod_does_not_get_production_issue_flag_or_score_100(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 42793,
            "subject": "BCP | Request for Session - Sesion para entender Reporte de Telemetria",
            "description": "Customer asked for a session to understand the telemetry report.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-11T14:58:42Z",
            "updated_at": "2026-03-11T16:14:20Z",
            "requester_id": 1001,
            "assignee_id": 2002,
            "tags": ["class_dev", "dev", "production_-_app_in_the_wild_with_condition"],
            "custom_fields": {
                "Status With": "Open Sales",
                "Support Stage": "Acknowledged",
                "Release Stage": "PROD",
                "Support Class": "Dev",
            },
        }
        comments = [
            {
                "author_id": 1001,
                "public": True,
                "body": "Hola Abraham, te escribo para solicitar una sesion para aprender a leer el reporte de telemetria.",
                "html_body": "<p>Hola Abraham, te escribo para solicitar una sesion para aprender a leer el reporte de telemetria.</p>",
                "created_at": "2026-03-11T14:58:42Z",
                "attachments": [],
            },
            {
                "author_id": 2002,
                "public": True,
                "body": "Claro, hoy por la tarde te queda bien? 1 o 2 pm de Peru les quedaria bien?",
                "html_body": "<p>Claro, hoy por la tarde te queda bien? 1 o 2 pm de Peru les quedaria bien?</p>",
                "created_at": "2026-03-11T15:11:32Z",
                "attachments": [],
            },
            {
                "author_id": 1001,
                "public": True,
                "body": "A las 2 estaria bien, enviare una invitacion para poder tener la grabacion local.",
                "html_body": "<p>A las 2 estaria bien, enviare una invitacion para poder tener la grabacion local.</p>",
                "created_at": "2026-03-11T16:05:05Z",
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
        self.assertFalse(assessment.production_impact.is_production_issue)
        self.assertNotIn("production_user_impact", flag_codes)
        self.assertLess(assessment.risk_score, 100)
        self.assertNotIn("ticket_report_request", flag_codes)

    def test_customer_ticket_report_request_is_highlighted(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 42802,
            "subject": "ACME | Support | Request for Zendesk ticket report",
            "description": "Customer asked for a report of tickets from Zendesk.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-11T14:58:42Z",
            "updated_at": "2026-03-11T16:14:20Z",
            "requester_id": 1001,
            "assignee_id": 2002,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "Acknowledged",
                "Release Stage": "PROD",
                "Support Class": "Support",
            },
        }
        comments = [
            {
                "author_id": 1001,
                "public": True,
                "body": "Please send me a Zendesk ticket report with all related tickets for this account.",
                "html_body": "<p>Please send me a Zendesk ticket report with all related tickets for this account.</p>",
                "created_at": "2026-03-11T14:58:42Z",
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
        self.assertIn("ticket_report_request", flag_codes)
        self.assertTrue(
            any(
                flag.code == "ticket_report_request" and "ticket report" in flag.message.lower()
                for flag in assessment.flags
            )
        )

    def test_customer_urgency_language_is_highlighted(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 42468,
            "subject": "ACME | Android | Login issue",
            "description": "Customer says this is urgent and needs attention ASAP.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-11T14:58:42Z",
            "updated_at": "2026-03-11T16:14:20Z",
            "requester_id": 1001,
            "assignee_id": 2002,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "Acknowledged",
                "Release Stage": "PROD",
                "Support Class": "Support",
            },
        }
        comments = [
            {
                "author_id": 1001,
                "public": True,
                "body": "This is a high priority issue for us. Please treat it as urgent.",
                "html_body": "<p>This is a high priority issue for us. Please treat it as urgent.</p>",
                "created_at": "2026-03-11T15:02:00Z",
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
        self.assertIn("customer_urgency", flag_codes)
        self.assertTrue(
            any(
                flag.code == "customer_urgency" and "urgent" in flag.message.lower()
                for flag in assessment.flags
            )
        )

    def test_customer_urgency_fast_track_go_live_language_is_highlighted(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 41904,
            "subject": "ACME | iOS | Release assistance",
            "description": "Customer is asking for release support.",
            "status": "open",
            "priority": "normal",
            "created_at": "2026-03-11T14:58:42Z",
            "updated_at": "2026-03-11T16:14:20Z",
            "requester_id": 1001,
            "assignee_id": 2002,
            "tags": [],
            "custom_fields": {
                "Status With": "support",
                "Support Stage": "Acknowledged",
                "Release Stage": "PROD",
                "Support Class": "Support",
            },
        }
        comments = [
            {
                "author_id": 1001,
                "public": True,
                "body": (
                    "Hello Team,\n\n"
                    "We are scheduled to go live tomorrow. Can you assist in fast-tracking this?\n\n"
                    "Kind Regards"
                ),
                "html_body": (
                    "<p>Hello Team,</p><p>We are scheduled to go live tomorrow. "
                    "Can you assist in fast-tracking this?</p><p>Kind Regards</p>"
                ),
                "created_at": "2026-03-11T15:02:00Z",
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
        self.assertIn("customer_urgency", flag_codes)
        self.assertTrue(
            any(
                flag.code == "customer_urgency"
                and ("fast-track" in flag.message.lower() or "go live" in flag.message.lower())
                for flag in assessment.flags
            )
        )

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

    def test_ticket_without_crash_detected_tag_ignores_crash_named_attachments(self) -> None:
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
        self.assertNotIn("crash_tag_missing_unreviewed_attachment_evidence", flag_codes)
        self.assertNotIn("crash_tag_missing", flag_codes)
        self.assertFalse(assessment.crash_attachment_summary.has_crash_related_attachments)

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

    def test_ticket_without_crash_detected_tag_does_not_flag_crash_subject(self) -> None:
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
        self.assertNotIn("crash_tag_missing", flag_codes)

    def test_ticket_without_crash_detected_tag_does_not_flag_crash_description(self) -> None:
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
        self.assertNotIn("crash_tag_missing", flag_codes)

    def test_get_ticket_summary_does_not_infer_crash_without_crash_detected_tag(self) -> None:
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
        self.assertIn("Crash-related attachments available:", summary_text)
        self.assertIn("Crash-related attachments available: No", summary_text)
        self.assertNotIn("crash_tag_missing", summary_text)
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

    def test_feature_request_ticket_summary_is_low_priority_and_no_risk(self) -> None:
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="get_ticket_summary",
                arguments={"ticket_id": 9914},
            ),
        )

        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket_payload = {
            "id": 9914,
            "subject": "ACME | iOS | Feature Request for dashboard export",
            "description": "Customer wants a new export option.",
            "status": "open",
            "priority": "high",
            "created_at": "2026-03-05T10:00:00Z",
            "updated_at": "2026-03-05T10:10:00Z",
            "requester_id": 1001,
            "tags": [],
            "custom_fields": {
                "Status With": "customer",
                "Support Stage": "investigation",
                "Release Stage": "Production",
            },
            "ticket_url": "https://appdomesupport.zendesk.com/agent/tickets/9914",
            "ticket_link": "[9914](https://appdomesupport.zendesk.com/agent/tickets/9914)",
        }
        comments_payload = [
            {
                "author_id": 1001,
                "public": True,
                "body": "This is a feature request for a future enhancement.",
                "html_body": "<p>This is a feature request for a future enhancement.</p>",
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
        self.assertIn("| Priority | low |", summary_text)
        self.assertIn("Production Issue: No", summary_text)
        self.assertIn(
            "Priority Interpretation: Feature request title detected: treat as low priority with no operational risk.",
            summary_text,
        )
        self.assertIn("In Trouble: No", summary_text)
        self.assertIn("Risk Score: 0", summary_text)
        self.assertIn("Flags: none", summary_text)
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

    def test_crash_ticket_generic_video_attachment_does_not_count_as_crash_evidence(self) -> None:
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
        self.assertFalse(assessment.crash_attachment_summary.has_crash_related_attachments)
        self.assertFalse(assessment.crash_attachment_summary.has_replication_video)
        self.assertEqual(assessment.crash_attachment_summary.replication_videos, [])

    def test_crash_ticket_video_with_crash_filename_counts_as_replication_evidence(self) -> None:
        with patch("zendesk_mcp_server.zendesk_client.Zenpy"):
            server_module = importlib.import_module("zendesk_mcp_server.server")

        ticket = {
            "id": 9914,
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
                "body": "Attached crash reproduction video.",
                "html_body": "<p>Attached crash reproduction video.</p>",
                "created_at": "2026-03-05T10:05:00Z",
                "attachments": [{"file_name": "android_crash_repro.mp4"}],
            }
        ]

        assessment = server_module._build_ticket_trouble_assessment(
            ticket=ticket,
            comments=comments,
            initial_response_sla_minutes=60,
            high_priority_stale_hours=8,
        )

        self.assertIsNotNone(assessment.crash_attachment_summary)
        self.assertTrue(assessment.crash_attachment_summary.has_crash_related_attachments)
        self.assertTrue(assessment.crash_attachment_summary.has_replication_video)
        self.assertIn("android_crash_repro.mp4", assessment.crash_attachment_summary.replication_videos)

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
            "tickets": [{"id": 501, "subject": "Facephi issue", "match_type": "exact"}],
            "page": 1,
            "per_page": 25,
            "count": 1,
            "sort_by": "updated_at",
            "sort_order": "desc",
            "query": 'type:ticket "Facephi" commenter:"Tom"',
            "exact_query": 'type:ticket "Facephi" commenter:"Tom"',
            "partial_query": None,
            "search_mode": "exact",
            "exact_count": 1,
            "partial_fallback_used": False,
            "partial_fallback_reason": None,
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
