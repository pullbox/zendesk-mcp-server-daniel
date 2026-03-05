import json
from typing import Any


def build_ticket_analysis_input(
    ticket_id: int,
    ticket: dict[str, Any],
    comments: list[dict[str, Any]],
    rubric: str,
) -> str:
    compact_comments = [
        {
            "id": comment.get("id"),
            "author_id": comment.get("author_id"),
            "public": comment.get("public"),
            "created_at": comment.get("created_at"),
            "body": comment.get("body"),
            "attachments": [
                {
                    "id": attachment.get("id"),
                    "file_name": attachment.get("file_name"),
                    "content_type": attachment.get("content_type"),
                    "size": attachment.get("size"),
                    "inline": attachment.get("inline"),
                }
                for attachment in (comment.get("attachments") or [])
            ],
        }
        for comment in comments
    ]

    payload = {
        "ticket_id": ticket_id,
        "ticket": ticket,
        "comments": compact_comments,
    }

    return (
        "Follow this QA analysis rubric exactly.\n\n"
        f"{rubric.strip()}\n\n"
        "Use the following evidence only.\n\n"
        f"{json.dumps(payload, indent=2)}"
    )


def build_batch_ticket_review_input(
    reviews: list[dict[str, Any]],
    rubric_template: str,
) -> str:
    batches = []
    for review in reviews:
        ticket_id = review["ticket_id"]
        batches.append(
            {
                "ticket_id": ticket_id,
                "rubric": rubric_template.format(ticket_id=ticket_id).strip(),
                "ticket": review["ticket"],
                "comments": [
                    {
                        "id": comment.get("id"),
                        "author_id": comment.get("author_id"),
                        "public": comment.get("public"),
                        "created_at": comment.get("created_at"),
                        "body": comment.get("body"),
                        "attachments": [
                            {
                                "id": attachment.get("id"),
                                "file_name": attachment.get("file_name"),
                                "content_type": attachment.get("content_type"),
                                "size": attachment.get("size"),
                                "inline": attachment.get("inline"),
                            }
                            for attachment in (comment.get("attachments") or [])
                        ],
                    }
                    for comment in review["comments"]
                ],
            }
        )

    return (
        "Review each sampled ticket independently.\n"
        "For each ticket, follow its rubric exactly and use only the provided evidence.\n"
        "Keep the reviews separate and clearly labeled by ticket id.\n\n"
        f"{json.dumps({'reviews': batches}, indent=2)}"
    )
