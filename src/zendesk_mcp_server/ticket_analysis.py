import json
from datetime import datetime, timedelta, timezone
from typing import Any

EST_TIMEZONE = timezone(timedelta(hours=-5), name="EST")
TIMESTAMP_FIELD_SUFFIXES = ("_at",)
TIMESTAMP_FIELD_NAMES = {
    "created",
    "updated",
    "timestamp",
}
ATTRIBUTION_GUARDRAILS = """
Evidence rules:
- Do not infer ownership, handoff, authorship, approval, escalation leadership, or decision-making unless the record explicitly states it.
- Do not treat a person's presence in comments, internal notes, meetings, customer calls, CC fields, or nearby text as evidence that they owned or drove the work.
- Describe observed actions literally and do not upgrade participation into ownership or responsibility.
- Ticket assignment alone does not prove who handled escalation work, and participation alone does not prove ownership transfer.
- State that ownership was transferred or handed off only when the record explicitly documents the transfer.
- If the record is incomplete or mixed, say "the record does not explicitly show" or "not explicitly documented" instead of filling the gap.
- Prefer omission over unsupported attribution.
""".strip()


def _format_est_timestamp(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone(EST_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S EST")


def _convert_timestamp_fields(value: Any, key: str | None = None) -> Any:
    if isinstance(value, list):
        return [_convert_timestamp_fields(item) for item in value]
    if isinstance(value, dict):
        return {k: _convert_timestamp_fields(v, key=k) for k, v in value.items()}
    if key and (key.endswith(TIMESTAMP_FIELD_SUFFIXES) or key in TIMESTAMP_FIELD_NAMES):
        return _format_est_timestamp(value)
    return value


def build_ticket_analysis_input(
    ticket_id: int,
    ticket: dict[str, Any],
    comments: list[dict[str, Any]],
    rubric: str,
    attachment_evidence_summary: dict[str, Any] | None = None,
    first_comment_context: dict[str, Any] | None = None,
    recent_comment_context: list[dict[str, Any]] | None = None,
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
        "ticket": _convert_timestamp_fields(ticket),
        "first_comment_context": _convert_timestamp_fields(first_comment_context or {}),
        "recent_comment_context": _convert_timestamp_fields(recent_comment_context or []),
        "comments": _convert_timestamp_fields(compact_comments),
        "attachment_evidence_summary": _convert_timestamp_fields(attachment_evidence_summary or {}),
    }

    return (
        "Follow this QA analysis rubric exactly.\n\n"
        f"{ATTRIBUTION_GUARDRAILS}\n\n"
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
        ticket_payload = review["ticket"] if isinstance(review.get("ticket"), dict) else {}
        ticket_link = ticket_payload.get("ticket_link") or f"#{ticket_id}"
        batches.append(
            {
                "ticket_id": ticket_id,
                "ticket_link": ticket_link,
                "production_issue": bool(
                    ticket_payload.get("production_impact", {}).get("is_production_issue")
                ),
                "rubric": rubric_template.format(ticket_id=ticket_id, ticket_link=ticket_link).strip(),
                "ticket": _convert_timestamp_fields(review["ticket"]),
                "first_comment_context": _convert_timestamp_fields(review.get("first_comment_context") or {}),
                "recent_comment_context": _convert_timestamp_fields(review.get("recent_comment_context") or []),
                "comments": _convert_timestamp_fields([
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
                ]),
                "attachment_evidence_summary": _convert_timestamp_fields(
                    review.get("attachment_evidence_summary") or {}
                ),
            }
        )

    return (
        "Review each sampled ticket independently.\n"
        "For each ticket, follow its rubric exactly and use only the provided evidence.\n"
        "Keep the reviews separate and clearly labeled by ticket id.\n\n"
        f"{ATTRIBUTION_GUARDRAILS}\n\n"
        f"{json.dumps({'reviews': batches}, indent=2)}"
    )
