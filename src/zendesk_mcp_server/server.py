import json
import logging
import os
from datetime import datetime, timezone
from typing import Annotated, Any

from cachetools.func import ttl_cache
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from zendesk_mcp_server.ticket_display import apply_ticket_field_displays
from zendesk_mcp_server.ticket_field_metadata import TicketFieldOptionResolver
from zendesk_mcp_server.zendesk_client import ZendeskClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("zendesk-mcp-server")
logger.info("zendesk mcp server started")

load_dotenv()
zendesk_client = ZendeskClient(
    subdomain=os.getenv("ZENDESK_SUBDOMAIN"),
    email=os.getenv("ZENDESK_EMAIL"),
    token=os.getenv("ZENDESK_API_KEY"),
)

mcp = FastMCP("Zendesk Server")
server = mcp


TITLE_REVIEW_POLICY_TEMPLATE = """
You are reviewing Zendesk ticket titles for naming-policy compliance.

Your task is to evaluate whether each ticket title follows the expected structure and is clear enough for internal support workflows.

Naming policy:
A Zendesk ticket title should generally follow one of these formats:
- <Customer Name> | OS Version | Ticket Subject
- <Customer Name> | Feature | Ticket Subject
- <Customer Name> | Third Party Tool | Ticket Subject

Allowed variations:
- Reasonable variations of the above are acceptable if the structure is still clear.
- Ignore case-only differences such as iOS vs IOS.
- If the ticket is a trial ticket, the word "Trial" may appear before the customer name.
- Minor wording differences are acceptable if the title still clearly communicates:
  1. who the customer is
  2. what platform, feature, or integration is involved
  3. what the issue or request is

Validation rules:
- A title is VALID if it clearly contains these core elements in a structured and readable format.
- A title is INVALID if it is missing a key element, is ambiguous, is poorly structured, or does not follow the expected segmented pattern closely enough.
- Prefer practical judgment over rigid literal matching.
- Do not fail a title only because of capitalization differences.
- Do not invent missing facts. If information is missing from the title, mark it invalid and explain what is missing.
- An escalated Ticket can only marked as solved when the customer confirmed that the provided solution worked.

When reviewing a title, return one line each and exactly:
Validation: VALID or INVALID
Reason: <brief explanation>
Suggested Title: <only if invalid>

Be consistent and concise.
If multiple tickets are reviewed, also include:
Summary: <count valid> valid, <count invalid> invalid
"""

REVIEW_SINGLE_TICKET_TEMPLATE = """
Use the ticket title review policy to review Zendesk ticket #{ticket_id}.

Instructions:
- Fetch the ticket first.
- Evaluate only the ticket title unless other ticket details are needed to understand obvious ambiguity.
- Apply the review policy exactly.
- Return the result in the required format.
"""

TICKET_ANALYSIS_TEMPLATE = """
You are reviewing Zendesk ticket #{ticket_id} for internal support QA.

Use only the ticket details and ticket comments as evidence. Do not infer or invent facts that are not explicitly present in the ticket data. If a milestone or detail cannot be found, write "Not found".

Review goals:
1. Identify whether the support handling appears compliant with internal processes based only on available evidence.
2. Highlight any gaps, delays, missing confirmations, or unclear ownership transitions.
3. Summarize what happened in a way that is useful for coaching and follow-up.

Required output:
1. Issue Summary
   Briefly summarize the customer issue and what the team did.
2. Current Status
   State the current ticket status and the latest known state.
3. Timeline
   Provide the following items, each on its own line:
   - Opened:
   - First agent response:
   - Escalated:
   - Solution built:
   - Solution delivered to customer:
   - Customer acknowledgement:
   Use exact timestamps when available. Otherwise write "Not found".
4. Process Review
   List concrete observations about process compliance or non-compliance based on evidence from the ticket and comments.
5. Compliance Score
   Give a score from 0 to 100.
   - 90-100: strong evidence of compliant handling
   - 70-89: mostly compliant with minor gaps
   - 40-69: notable process gaps or unclear evidence
   - 0-39: major process failures or missing critical handling steps
   Include a short explanation for the score.

Rules:
- Do not use external assumptions or general policy knowledge unless explicitly present in the ticket.
- Do not treat missing evidence as completed work.
- If the customer has not explicitly confirmed the solution worked, do not mark the resolution as customer-acknowledged.
- Prefer concise, evidence-based statements.
"""

COMMENT_DRAFT_TEMPLATE = """
You are a helpful Zendesk support agent. You need to draft a response to ticket #{ticket_id}.

Please fetch the ticket info, comments and knowledge base to draft a professional and helpful response that:
1. Acknowledges the customer's concern
2. Addresses the specific issues raised
3. Provides clear next steps or ask for specific details need to proceed
4. Maintains a friendly and professional tone
5. Ask for confirmation before commenting on the ticket

The response should be formatted well and ready to be posted as a comment.
"""

ticket_field_option_resolver = TicketFieldOptionResolver(zendesk_client)
ticket_field_option_resolver.load()


def _prepare_ticket_payload(ticket_id: int) -> dict[str, Any]:
    ticket = zendesk_client.get_ticket(ticket_id)
    ticket = apply_ticket_field_displays(ticket, ticket_field_option_resolver)
    return ticket


def _format_display_datetime(value: str | None) -> str:
    if not value:
        return "N/A"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%B %-d, %Y at %H:%M UTC")
    except ValueError:
        return value


def _build_ticket_summary(ticket: dict[str, Any]) -> str:
    custom_fields = ticket.get("custom_fields", {})
    lines = [
        f"# Ticket #{ticket.get('id')} - {ticket.get('subject', 'Untitled')}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Subject | {ticket.get('subject', 'N/A')} |",
        f"| Status | {ticket.get('status', 'N/A')} |",
        f"| Priority | {ticket.get('priority', 'N/A')} |",
        f"| Created | {_format_display_datetime(ticket.get('created_at'))} |",
        f"| Last Updated | {_format_display_datetime(ticket.get('updated_at'))} |",
        "",
        "## Custom Fields",
    ]

    custom_field_order = [
        "Status With",
        "Support Stage",
        "Release Stage",
        "Escalation Status",
        "Support Class",
        "Priority",
    ]
    for field_name in custom_field_order:
        value = custom_fields.get(field_name, "N/A")
        lines.append(f"{field_name}: {value}")

    if ticket.get("escalation_status_display"):
        lines.append(f"Escalation Status Display: {ticket['escalation_status_display']}")

    return "\n".join(lines)

class TicketItem(BaseModel):
    id: int | None = None
    subject: str | None = None
    status: str | None = None
    priority: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class TicketFilters(BaseModel):
    agent: str | None = None
    organization: str | None = None
    updated_since: str | None = None
    last_hours: int | None = None
    stale_hours: int | None = None
    include_solved: bool = False


class GetTicketsResult(BaseModel):
    tickets: list[TicketItem]
    page: int
    per_page: int
    count: int
    sort_by: str
    sort_order: str
    filters: TicketFilters | None = None
    has_more: bool
    next_page: int | None = None
    previous_page: int | None = None


@mcp.prompt(name="analyze-ticket", description="Analyze a Zendesk ticket and provide insights")
def analyze_ticket_prompt(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to analyze")],
) -> str:
    return TICKET_ANALYSIS_TEMPLATE.format(ticket_id=ticket_id).strip()


@mcp.prompt(
    name="draft-ticket-response",
    description="Draft a professional response to a Zendesk ticket",
)

@mcp.prompt(
    name="ticket-title-review-policy",
    description="Define the policy for reviewing Zendesk ticket title structure",
)
def ticket_title_review_policy_prompt() -> str:
    return TITLE_REVIEW_POLICY_TEMPLATE.strip()

@mcp.prompt(
    name="review-ticket-title",
    description="Review a specific Zendesk ticket title using the title review policy",
)
def review_ticket_title_prompt(
    ticket_id: Annotated[int, Field(description="The Zendesk ticket ID to review")],
) -> str:
    return (
        TITLE_REVIEW_POLICY_TEMPLATE.strip()
        + "\n\n"
        + REVIEW_SINGLE_TICKET_TEMPLATE.format(ticket_id=ticket_id).strip()
    )


def draft_ticket_response_prompt(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to respond to")],
) -> str:
    return COMMENT_DRAFT_TEMPLATE.format(ticket_id=ticket_id).strip()


@mcp.tool(name="get_ticket", description="Retrieve a Zendesk ticket by its ID")
def get_ticket(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to retrieve")],
) -> str:
    ticket = _prepare_ticket_payload(ticket_id)
    return json.dumps(ticket)


@mcp.tool(
    name="get_ticket_summary",
    description="Retrieve a Zendesk ticket as a compact display-ready summary",
)
def get_ticket_summary(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to summarize")],
) -> str:
    ticket = _prepare_ticket_payload(ticket_id)
    return _build_ticket_summary(ticket)


@mcp.tool(name="create_ticket", description="Create a new Zendesk ticket")
def create_ticket(
    subject: Annotated[str, Field(description="Ticket subject")],
    description: Annotated[str, Field(description="Ticket description")],
    requester_id: Annotated[int | None, Field(description="Requester user ID")] = None,
    assignee_id: Annotated[int | None, Field(description="Assignee user ID")] = None,
    priority: Annotated[str | None, Field(description="low, normal, high, urgent")] = None,
    type: Annotated[str | None, Field(description="problem, incident, question, task")] = None,
    tags: Annotated[list[str] | None, Field(description="Optional ticket tags")] = None,
    custom_fields: Annotated[list[dict[str, Any]] | None, Field(description="Optional custom field values")] = None,
) -> str:
    created = zendesk_client.create_ticket(
        subject=subject,
        description=description,
        requester_id=requester_id,
        assignee_id=assignee_id,
        priority=priority,
        type=type,
        tags=tags,
        custom_fields=custom_fields,
    )
    return json.dumps({"message": "Ticket created successfully", "ticket": created}, indent=2)


@mcp.tool(
    name="get_tickets",
    description="Fetch a lightweight summary list of the latest tickets with pagination support",
    structured_output=True,
)
def get_tickets(
    page: Annotated[int, Field(description="Page number")] = 1,
    per_page: Annotated[int, Field(description="Number of tickets per page (max 100)")] = 25,
    sort_by: Annotated[
        str,
        Field(description="Field to sort by (created_at, updated_at, priority, status)"),
    ] = "created_at",
    sort_order: Annotated[str, Field(description="Sort order (asc or desc)")] = "desc",
    agent: Annotated[
        str | None,
        Field(description="Optional assignee filter. Can be agent id, email, or name."),
    ] = None,
    organization: Annotated[
        str | None,
        Field(description="Optional organization name filter."),
    ] = None,
    updated_since: Annotated[
        str | None,
        Field(description="ISO date/datetime filter, e.g. 2026-02-26T10:00:00Z."),
    ] = None,
    last_hours: Annotated[
        int | None,
        Field(description="Relative filter. Example: 5 = updated in last 5 hours."),
    ] = None,
    stale_hours: Annotated[
        int | None,
        Field(description="Stale detector. Example: 24 = not updated in the last 24 hours."),
    ] = None,
    include_solved: Annotated[
        bool,
        Field(description="Include solved/closed tickets in stale detection results."),
    ] = False,
) -> GetTicketsResult:
    tickets = zendesk_client.get_tickets(
        page=page,
        per_page=per_page,
        sort_by=sort_by,
        sort_order=sort_order,
        agent=agent,
        organization=organization,
        updated_since=updated_since,
        last_hours=last_hours,
        stale_hours=stale_hours,
        include_solved=include_solved,
    )
    return GetTicketsResult.model_validate(tickets)


@mcp.tool(name="get_ticket_comments", description="Retrieve all comments for a Zendesk ticket by its ID")
def get_ticket_comments(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to get comments for")],
) -> str:
    comments = zendesk_client.get_ticket_comments(ticket_id)
    return json.dumps(comments)


@mcp.tool(name="create_ticket_comment", description="Create a new comment on an existing Zendesk ticket")
def create_ticket_comment(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to comment on")],
    comment: Annotated[str, Field(description="The comment text/content to add")],
    public: Annotated[bool, Field(description="Whether the comment should be public")] = True,
) -> str:
    result = zendesk_client.post_comment(ticket_id=ticket_id, comment=comment, public=public)
    return f"Comment created successfully: {result}"


@mcp.tool(
    name="get_ticket_fields",
    description="List all Zendesk ticket fields, including IDs, names, and types.",
)
def get_ticket_fields() -> str:
    fields = zendesk_client.get_ticket_fields()
    return json.dumps(fields, indent=2)


@mcp.tool(
    name="update_ticket",
    description="Update fields on an existing Zendesk ticket (e.g., status, priority, assignee_id)",
)
def update_ticket(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to update")],
    subject: Annotated[str | None, Field(description="Updated subject")] = None,
    status: Annotated[
        str | None,
        Field(description="new, open, pending, on-hold, solved, closed"),
    ] = None,
    priority: Annotated[str | None, Field(description="low, normal, high, urgent")] = None,
    type: Annotated[str | None, Field(description="problem, incident, question, task")] = None,
    assignee_id: Annotated[int | None, Field(description="Updated assignee ID")] = None,
    requester_id: Annotated[int | None, Field(description="Updated requester ID")] = None,
    tags: Annotated[list[str] | None, Field(description="Updated ticket tags")] = None,
    custom_fields: Annotated[list[dict[str, Any]] | None, Field(description="Updated custom fields")] = None,
    due_at: Annotated[str | None, Field(description="ISO8601 due date")] = None,
) -> str:
    update_fields = {
        key: value
        for key, value in {
            "subject": subject,
            "status": status,
            "priority": priority,
            "type": type,
            "assignee_id": assignee_id,
            "requester_id": requester_id,
            "tags": tags,
            "custom_fields": custom_fields,
            "due_at": due_at,
        }.items()
        if value is not None
    }
    updated = zendesk_client.update_ticket(ticket_id=ticket_id, **update_fields)
    return json.dumps({"message": "Ticket updated successfully", "ticket": updated}, indent=2)


@mcp.resource(
    "zendesk://knowledge-base",
    name="Zendesk Knowledge Base",
    description="Access to Zendesk Help Center articles and sections",
    mime_type="application/json",
)
def knowledge_base_resource() -> str:
    kb_data = get_cached_kb()
    return json.dumps(
        {
            "knowledge_base": kb_data,
            "metadata": {
                "sections": len(kb_data),
                "total_articles": sum(len(section["articles"]) for section in kb_data.values()),
            },
        },
        indent=2,
    )


@ttl_cache(ttl=3600)
def get_cached_kb() -> dict[str, Any]:
    return zendesk_client.get_all_articles()


def main() -> None:
    mcp.run(transport="stdio")
