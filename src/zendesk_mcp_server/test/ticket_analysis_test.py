import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from zendesk_mcp_server.ticket_analysis import build_batch_ticket_review_input, build_ticket_analysis_input


class TestTicketAnalysisInput(unittest.TestCase):
    def test_build_ticket_analysis_input_compacts_comments_and_embeds_rubric(self) -> None:
        text = build_ticket_analysis_input(
            ticket_id=42265,
            ticket={"id": 42265, "subject": "Developer Mode Detection"},
            comments=[
                {
                    "id": 1,
                    "author_id": 10,
                    "public": True,
                    "created_at": "2026-02-24T10:00:00Z",
                    "body": "Initial response",
                    "html_body": "<p>Initial response</p>",
                    "attachments": [
                        {
                            "id": 9001,
                            "file_name": "crash.ips",
                            "content_type": "text/plain",
                            "size": 1234,
                            "inline": False,
                        }
                    ],
                }
            ],
            rubric="Review ticket 42265 exactly.",
        )

        self.assertIn("Review ticket 42265 exactly.", text)
        self.assertIn('"ticket_id": 42265', text)
        self.assertIn('"body": "Initial response"', text)
        self.assertNotIn("html_body", text)

        payload = json.loads(text.split("Use the following evidence only.\n\n", 1)[1])
        self.assertEqual(payload["ticket"]["id"], 42265)
        self.assertEqual(payload["comments"][0]["author_id"], 10)
        self.assertEqual(payload["comments"][0]["attachments"][0]["file_name"], "crash.ips")

    def test_build_batch_ticket_review_input_embeds_multiple_reviews(self) -> None:
        text = build_batch_ticket_review_input(
            reviews=[
                {
                    "ticket_id": 100,
                    "ticket": {"id": 100, "subject": "First"},
                    "comments": [
                        {
                            "id": 1,
                            "author_id": 10,
                            "body": "One",
                            "attachments": [{"id": 1, "file_name": "android.log"}],
                        }
                    ],
                },
                {
                    "ticket_id": 200,
                    "ticket": {"id": 200, "subject": "Second"},
                    "comments": [{"id": 2, "author_id": 20, "body": "Two"}],
                },
            ],
            rubric_template="Review ticket #{ticket_id}.",
        )

        self.assertIn("Review each sampled ticket independently.", text)
        payload = json.loads(text.split("\n\n", 1)[1])
        self.assertEqual([review["ticket_id"] for review in payload["reviews"]], [100, 200])
        self.assertEqual(payload["reviews"][0]["rubric"], "Review ticket #100.")
        self.assertEqual(payload["reviews"][1]["comments"][0]["body"], "Two")
        self.assertEqual(payload["reviews"][0]["comments"][0]["attachments"][0]["file_name"], "android.log")
        self.assertEqual(payload["reviews"][0]["ticket_link"], "#100")

    def test_build_batch_ticket_review_input_supports_ticket_link_placeholder(self) -> None:
        text = build_batch_ticket_review_input(
            reviews=[
                {
                    "ticket_id": 300,
                    "ticket": {
                        "id": 300,
                        "subject": "Third",
                        "ticket_link": "[300](https://example.zendesk.com/agent/tickets/300)",
                    },
                    "comments": [],
                }
            ],
            rubric_template="Review ticket {ticket_link}.",
        )

        payload = json.loads(text.split("\n\n", 1)[1])
        self.assertEqual(payload["reviews"][0]["ticket_link"], "[300](https://example.zendesk.com/agent/tickets/300)")
        self.assertEqual(payload["reviews"][0]["rubric"], "Review ticket [300](https://example.zendesk.com/agent/tickets/300).")


if __name__ == "__main__":
    unittest.main()
