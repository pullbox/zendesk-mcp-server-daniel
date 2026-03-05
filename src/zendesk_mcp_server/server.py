import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from cachetools.func import ttl_cache
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from zendesk_mcp_server.ticket_analysis import build_batch_ticket_review_input, build_ticket_analysis_input
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
ZENDESK_TICKET_LINK_BASE_URL = os.getenv(
    "ZENDESK_TICKET_LINK_BASE_URL",
    "https://appdomesupport.zendesk.com/agent/tickets",
)


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
- A middle segment like "Platform" or "OS" is acceptable as platform context, even without a specific platform/version value.
- Minor wording differences are acceptable if the title still clearly communicates:
  1. who the customer is
  2. what platform, feature, or integration is involved
  3. what the issue or request is

Validation rules:
- A title is VALID if it clearly contains these core elements in a structured and readable format.
- A title is INVALID if it is missing a key element, is ambiguous, is poorly structured, or does not follow the expected segmented pattern closely enough.
- Prefer practical judgment over rigid literal matching.
- Do not fail a title only because of capitalization differences.
- Do not mark platform as missing when a dedicated platform category segment (for example "Platform" or "OS") is present.
- Do not invent missing facts. If information is missing from the title, mark it invalid and explain what is missing.
- An escalated Ticket (the Escalation Status field is populated) can only marked as solved when the customer confirmed that the provided solution worked.

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
   - Crash identified:
   - Stacktrace requested:
   - Escalated:
   - Time to escalation from ticket creation:
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
- Ticket Title is formated correctly.
- Escalated Tickets are tickets where the Escalation Status field is populated.
- Do not use external assumptions or general policy knowledge unless explicitly present in the ticket.
- Do not treat missing evidence as completed work.
- For Escalated Tickets, if the customer has not explicitly confirmed the solution worked, do not mark the resolution as customer-acknowledged.
- Crash ticket rule: if the ticket has tag "crash_detected", verify crash evidence handling.
- For crash_detected tickets, treat stacktrace evidence as present only when there is explicit stacktrace content in comments or a relevant crash attachment (for example .ips, .crash, .log, .txt, .dmp).
- If a crash_detected ticket has no stacktrace evidence, verify the assigned support engineer asked the customer for stacktrace/crash log details. If no such request appears in comments, flag this as a process gap.
- For crash_detected tickets, enforce stacktrace request timeliness: if stacktrace evidence is not already present, the first explicit support request for stacktrace/crash logs should occur within 1 hour of crash identification.
- For this check, infer crash identification time from the earliest explicit crash evidence in the ticket/comments; if the ticket already has tag "crash_detected", use ticket created timestamp when no earlier signal is available.
- If the first stacktrace request is more than 1 hour after crash identification, explicitly flag "Late stacktrace request (>1h)" in Process Review and reduce the Compliance Score.
- For crash_detected tickets, always calculate and report "Time to escalation from ticket creation" using ticket created timestamp and the first explicit escalation timestamp in the evidence.
- If escalation evidence exists but no escalation timestamp can be determined, write "Not found" and explicitly flag this as a process gap.
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


def _ticket_url(ticket_id: int | None) -> str | None:
    if ticket_id is None:
        return None
    return f"{ZENDESK_TICKET_LINK_BASE_URL}/{ticket_id}"


def _ticket_link(ticket_id: int | None) -> str | None:
    ticket_url = _ticket_url(ticket_id)
    if ticket_url is None:
        return None
    return f"[{ticket_id}]({ticket_url})"


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
    ticket_id = ticket.get("id")
    ticket_link = _ticket_link(ticket_id) or f"#{ticket_id}"
    lines = [
        f"# Ticket {ticket_link} - {ticket.get('subject', 'Untitled')}",
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
    ticket_url: str | None = None
    ticket_link: str | None = None
    subject: str | None = None
    status: str | None = None
    priority: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    stale_age_hours: int | None = None
    stale_age_days: int | None = None


class TicketFilters(BaseModel):
    agent: str | None = None
    organization: str | None = None
    updated_since: str | None = None
    last_hours: int | None = None
    created_last_hours: int | None = None
    stale_hours: int | None = None
    include_solved: bool = False
    exclude_internal: bool = False


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


class SearchTicketsByTextFilters(BaseModel):
    phrase: str
    organization: str | None = None
    updated_since: str | None = None
    updated_before: str | None = None
    status: str | None = None
    include_solved: bool = False
    exclude_internal: bool = False
    comment_author: str | None = None


class SearchTicketsByTextResult(BaseModel):
    tickets: list[TicketItem]
    page: int
    per_page: int
    count: int
    sort_by: str
    sort_order: str
    query: str
    filters: SearchTicketsByTextFilters
    has_more: bool
    next_page: int | None = None
    previous_page: int | None = None


class RandomTicketSampleResult(BaseModel):
    tickets: list[TicketItem]
    requested_count: int
    sampled_count: int
    total_matches: int
    retrieved_count: int
    truncated: bool
    exclude_api_created: bool = False
    excluded_api_created_count: int = 0
    agent: str
    solved_after: str
    solved_before: str
    seed: int | None = None


class RandomTicketReviewResult(BaseModel):
    sampled_ticket_ids: list[int]
    sampled_ticket_urls: list[str]
    sampled_ticket_links: list[str]
    sampled_count: int
    total_matches: int
    retrieved_count: int
    truncated: bool
    exclude_api_created: bool = False
    excluded_api_created_count: int = 0
    agent: str
    solved_after: str
    solved_before: str
    seed: int | None = None
    review_input: str


class TicketTroubleFlag(BaseModel):
    code: str
    severity: str
    message: str


class TicketTroubleAssessment(BaseModel):
    ticket_id: int
    ticket_url: str
    ticket_link: str
    subject: str | None = None
    status: str | None = None
    priority: str | None = None
    in_trouble: bool
    risk_score: int
    flags: list[TicketTroubleFlag]


class ScanTicketsInTroubleResult(BaseModel):
    created_last_hours: int
    scanned_count: int
    in_trouble_count: int
    tickets: list[TicketTroubleAssessment]


TROUBLE_FLAG_WEIGHTS: dict[str, int] = {
    "missing_initial_response": 34,
    "crash_process_gap": 45,
    "status_fields_incomplete": 24,
    "customer_comment_no_response": 30,
    "solved_without_customer_confirmation": 10,
    "high_priority_no_recent_updates": 25,
    "late_initial_response": 20,
    "late_stacktrace_request": 44,
    "title_incorrect": 45,
}
SEVERITY_FALLBACK_WEIGHTS = {"high": 30, "medium": 15, "low": 5}
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
CUSTOMER_FOLLOW_UP_SLA_HOURS = 4
CUSTOMER_FOLLOW_UP_PRIORITIES = {"low", "normal"}
NO_RESPONSE_EXPECTED_OPEN_STALE_DAYS = 5
OPEN_TICKET_STATUSES = {"new", "open", "pending", "hold", "on-hold"}


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _contains_any(text: str | None, terms: list[str]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _is_title_structured(subject: str | None) -> bool:
    if not subject:
        return False
    segments = [segment.strip() for segment in subject.split("|")]
    segments = [segment for segment in segments if segment]
    return len(segments) >= 3


def _is_no_response_expected_comment(comment: dict[str, Any]) -> bool:
    body = str(comment.get("body") or "").lower()
    html_body = str(comment.get("html_body") or "").lower()
    text = f"{body} {html_body}"
    no_response_expected_terms = [
        "you can close",
        "please close",
        "feel free to close",
        "thanks, this worked",
        "thank you, this worked",
        "resolved, thanks",
        "i will get back to you",
    ]
    return any(term in text for term in no_response_expected_terms)


def _build_ticket_trouble_assessment(
    ticket: dict[str, Any],
    comments: list[dict[str, Any]],
    initial_response_sla_minutes: int,
    high_priority_stale_hours: int,
) -> TicketTroubleAssessment:
    flags: list[TicketTroubleFlag] = []

    ticket_id = int(ticket.get("id"))
    subject = ticket.get("subject")
    status = ticket.get("status")
    priority = ticket.get("priority")
    requester_id = ticket.get("requester_id")
    tags = set(ticket.get("tags") or [])
    created_at = _parse_iso_datetime(ticket.get("created_at"))
    updated_at = _parse_iso_datetime(ticket.get("updated_at"))
    custom_fields = ticket.get("custom_fields") if isinstance(ticket.get("custom_fields"), dict) else {}

    public_comments = [c for c in comments if c.get("public")]
    public_comments_sorted = sorted(
        public_comments,
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )

    if not _is_title_structured(subject):
        flags.append(
            TicketTroubleFlag(
                code="title_incorrect",
                severity="medium",
                message="Ticket title is missing expected structured segments (Customer | Context | Issue).",
            )
        )

    required_status_fields = ["Status With", "Support Stage", "Release Stage"]
    missing_status_fields = [field for field in required_status_fields if not custom_fields.get(field)]
    if missing_status_fields:
        flags.append(
            TicketTroubleFlag(
                code="status_fields_incomplete",
                severity="high",
                message=f"Required status fields missing/empty: {', '.join(missing_status_fields)}.",
            )
        )

    first_public_agent_response_at: datetime | None = None
    for comment in public_comments_sorted:
        author_id = comment.get("author_id")
        if requester_id is not None and author_id == requester_id:
            continue
        first_public_agent_response_at = _parse_iso_datetime(comment.get("created_at"))
        if first_public_agent_response_at is not None:
            break

    if created_at is not None and first_public_agent_response_at is None:
        flags.append(
            TicketTroubleFlag(
                code="missing_initial_response",
                severity="high",
                message="No public agent response found.",
            )
        )
    elif created_at is not None and first_public_agent_response_at is not None:
        response_minutes = int((first_public_agent_response_at - created_at).total_seconds() // 60)
        if response_minutes > initial_response_sla_minutes:
            flags.append(
                TicketTroubleFlag(
                    code="late_initial_response",
                    severity="high",
                    message=f"Initial public response took {response_minutes}m (SLA {initial_response_sla_minutes}m).",
                )
            )

    customer_public_comments = [
        c for c in public_comments_sorted if requester_id is not None and c.get("author_id") == requester_id
    ]
    if priority in CUSTOMER_FOLLOW_UP_PRIORITIES:
        follow_up_deadline = timedelta(hours=CUSTOMER_FOLLOW_UP_SLA_HOURS)
        for customer_comment in customer_public_comments:
            customer_time = _parse_iso_datetime(customer_comment.get("created_at"))
            if customer_time is None:
                continue
            has_follow_up = False
            first_follow_up_after_customer: datetime | None = None
            for possible_reply in public_comments_sorted:
                reply_time = _parse_iso_datetime(possible_reply.get("created_at"))
                if reply_time is None:
                    continue
                if reply_time <= customer_time:
                    continue
                if requester_id is not None and possible_reply.get("author_id") == requester_id:
                    continue
                has_follow_up = True
                first_follow_up_after_customer = reply_time
                break

            if _is_no_response_expected_comment(customer_comment):
                reference_time = updated_at or datetime.now(timezone.utc)
                has_any_later_public_comment = any(
                    (_parse_iso_datetime(possible_reply.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
                    > customer_time
                    for possible_reply in public_comments_sorted
                )
                if (
                    status in OPEN_TICKET_STATUSES
                    and (reference_time - customer_time) > timedelta(days=NO_RESPONSE_EXPECTED_OPEN_STALE_DAYS)
                    and not has_any_later_public_comment
                ):
                    flags.append(
                        TicketTroubleFlag(
                            code="customer_comment_no_response",
                            severity="high",
                            message=(
                                "Ticket stayed open more than "
                                f"{NO_RESPONSE_EXPECTED_OPEN_STALE_DAYS} days after a no-response-expected "
                                "customer update, with no later public comments."
                            ),
                        )
                    )
                    break
                continue

            if has_follow_up and first_follow_up_after_customer is not None:
                response_delay = first_follow_up_after_customer - customer_time
                if response_delay > follow_up_deadline:
                    flags.append(
                        TicketTroubleFlag(
                            code="customer_comment_no_response",
                            severity="high",
                            message=(
                                "Customer public comment did not receive a public agent response "
                                f"within {CUSTOMER_FOLLOW_UP_SLA_HOURS}h."
                            ),
                        )
                    )
                    break
                continue

            reference_time = updated_at or datetime.now(timezone.utc)
            if reference_time - customer_time > follow_up_deadline:
                flags.append(
                    TicketTroubleFlag(
                        code="customer_comment_no_response",
                        severity="high",
                        message=(
                            "Customer public comment did not receive a public agent response "
                            f"within {CUSTOMER_FOLLOW_UP_SLA_HOURS}h."
                        ),
                    )
                )
                break

    confirmation_terms = ["resolved", "works", "working", "fixed", "thank", "confirmed", "solved"]
    has_customer_confirmation = any(
        _contains_any(c.get("body"), confirmation_terms) or _contains_any(c.get("html_body"), confirmation_terms)
        for c in customer_public_comments
    )
    if status in {"solved", "closed"} and not has_customer_confirmation:
        flags.append(
            TicketTroubleFlag(
                code="solved_without_customer_confirmation",
                severity="high",
                message="Ticket is solved/closed without explicit customer confirmation in public comments.",
            )
        )

    if priority in {"high", "urgent"}:
        stale_hours = ticket.get("stale_age_hours")
        if stale_hours is None and updated_at is not None:
            stale_hours = int(max((datetime.now(timezone.utc) - updated_at).total_seconds(), 0) // 3600)
        if stale_hours is not None and int(stale_hours) > high_priority_stale_hours:
            flags.append(
                TicketTroubleFlag(
                    code="high_priority_no_recent_updates",
                    severity="high",
                    message=(
                        f"High-priority ticket has no recent update for {int(stale_hours)}h "
                        f"(threshold {high_priority_stale_hours}h)."
                    ),
                )
            )

    if "crash_detected" in tags:
        evidence_extensions = (".ips", ".crash", ".log", ".txt", ".dmp")
        evidence_terms = ["stacktrace", "stack trace", "backtrace", "crash log", "exception"]
        request_terms = ["send stacktrace", "share stacktrace", "provide stacktrace", "crash log", "stack trace"]

        has_stacktrace_evidence = False
        first_stacktrace_request_at: datetime | None = None

        for comment in public_comments_sorted:
            body = comment.get("body")
            html_body = comment.get("html_body")
            attachments = comment.get("attachments") or []

            if _contains_any(body, evidence_terms) or _contains_any(html_body, evidence_terms):
                has_stacktrace_evidence = True

            for attachment in attachments:
                file_name = str(attachment.get("file_name") or "").lower()
                if file_name.endswith(evidence_extensions):
                    has_stacktrace_evidence = True
                    break

            if first_stacktrace_request_at is None and _contains_any(body, request_terms):
                first_stacktrace_request_at = _parse_iso_datetime(comment.get("created_at"))

        if not has_stacktrace_evidence and first_stacktrace_request_at is None:
            flags.append(
                TicketTroubleFlag(
                    code="crash_process_gap",
                    severity="high",
                    message="Crash ticket has no stacktrace evidence and no explicit request for crash logs.",
                )
            )
        elif created_at is not None and first_stacktrace_request_at is not None:
            request_delay_minutes = int((first_stacktrace_request_at - created_at).total_seconds() // 60)
            if request_delay_minutes > 60 and not has_stacktrace_evidence:
                flags.append(
                    TicketTroubleFlag(
                        code="late_stacktrace_request",
                        severity="medium",
                        message=f"Stacktrace request was sent after {request_delay_minutes}m (>60m).",
                    )
                )

    sorted_flags = sorted(
        flags,
        key=lambda flag: (
            -TROUBLE_FLAG_WEIGHTS.get(flag.code, SEVERITY_FALLBACK_WEIGHTS.get(flag.severity, 5)),
            -SEVERITY_RANK.get(flag.severity, 0),
            flag.code,
        ),
    )
    risk_score = min(
        100,
        sum(
            TROUBLE_FLAG_WEIGHTS.get(flag.code, SEVERITY_FALLBACK_WEIGHTS.get(flag.severity, 5))
            for flag in sorted_flags
        ),
    )

    return TicketTroubleAssessment(
        ticket_id=ticket_id,
        ticket_url=_ticket_url(ticket_id) or "",
        ticket_link=_ticket_link(ticket_id) or "",
        subject=subject,
        status=status,
        priority=priority,
        in_trouble=bool(sorted_flags),
        risk_score=risk_score,
        flags=sorted_flags,
    )


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


@mcp.tool(
    name="review_ticket",
    description="Fetch ticket evidence and the review rubric for a Zendesk ticket",
)
def review_ticket(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to review")],
) -> str:
    ticket = _prepare_ticket_payload(ticket_id)
    comments = zendesk_client.get_ticket_comments(ticket_id)
    return build_ticket_analysis_input(
        ticket_id=ticket_id,
        ticket=ticket,
        comments=comments,
        rubric=TICKET_ANALYSIS_TEMPLATE.format(ticket_id=ticket_id),
    )


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
    created_last_hours: Annotated[
        int | None,
        Field(description="Relative filter. Example: 4 = created in last 4 hours."),
    ] = None,
    stale_hours: Annotated[
        int | None,
        Field(description="Stale detector. Example: 24 = not updated in the last 24 hours."),
    ] = None,
    include_solved: Annotated[
        bool,
        Field(description="Include solved/closed tickets in stale detection results."),
    ] = False,
    exclude_internal: Annotated[
        bool,
        Field(description="Exclude tickets tagged internal from search results."),
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
        created_last_hours=created_last_hours,
        stale_hours=stale_hours,
        include_solved=include_solved,
        exclude_internal=exclude_internal,
    )
    return GetTicketsResult.model_validate(tickets)


@mcp.tool(
    name="scan_tickets_in_trouble",
    description="Scan recently created tickets and flag tickets likely in trouble based on QA process checks",
    structured_output=True,
)
def scan_tickets_in_trouble(
    created_last_hours: Annotated[
        int,
        Field(description="Scan tickets created in the last N hours."),
    ] = 4,
    per_page: Annotated[
        int,
        Field(description="How many tickets to inspect from the created window (max 100)."),
    ] = 50,
    exclude_internal: Annotated[
        bool,
        Field(description="Exclude tickets tagged internal from scan results."),
    ] = True,
    initial_response_sla_minutes: Annotated[
        int,
        Field(description="SLA threshold for first public agent response in minutes."),
    ] = 60,
    high_priority_stale_hours: Annotated[
        int,
        Field(description="Threshold for stale high-priority tickets in hours."),
    ] = 8,
) -> ScanTicketsInTroubleResult:
    list_result = zendesk_client.get_tickets(
        page=1,
        per_page=min(per_page, 100),
        sort_by="created_at",
        sort_order="desc",
        created_last_hours=created_last_hours,
        exclude_internal=exclude_internal,
    )

    assessments: list[TicketTroubleAssessment] = []
    for ticket in list_result.get("tickets", []):
        if str(ticket.get("status", "")).lower() == "solved":
            continue
        ticket_id = ticket.get("id")
        if ticket_id is None:
            continue
        full_ticket = _prepare_ticket_payload(int(ticket_id))
        comments = zendesk_client.get_ticket_comments(int(ticket_id))
        assessment = _build_ticket_trouble_assessment(
            ticket=full_ticket,
            comments=comments,
            initial_response_sla_minutes=initial_response_sla_minutes,
            high_priority_stale_hours=high_priority_stale_hours,
        )
        assessments.append(assessment)

    assessments.sort(
        key=lambda ticket: (ticket.in_trouble, ticket.risk_score, ticket.ticket_id),
        reverse=True,
    )
    in_trouble_count = len([ticket for ticket in assessments if ticket.in_trouble])
    return ScanTicketsInTroubleResult(
        created_last_hours=created_last_hours,
        scanned_count=len(assessments),
        in_trouble_count=in_trouble_count,
        tickets=assessments,
    )


@mcp.tool(
    name="search_tickets_by_text",
    description="Search ticket descriptions/comments by phrase, with optional organization/timeframe and comment-author filters",
    structured_output=True,
)
def search_tickets_by_text(
    phrase: Annotated[str, Field(description="Text or phrase to search for, e.g. Facephi.")],
    page: Annotated[int, Field(description="Page number")] = 1,
    per_page: Annotated[int, Field(description="Number of tickets per page (max 100)")] = 25,
    sort_by: Annotated[str, Field(description="Field to sort by (updated_at, created_at, priority, status)")] = "updated_at",
    sort_order: Annotated[str, Field(description="Sort order (asc or desc)")] = "desc",
    organization: Annotated[str | None, Field(description="Optional organization name filter.")] = None,
    updated_since: Annotated[
        str | None,
        Field(description="Optional inclusive lower bound for updated timestamp/date."),
    ] = None,
    updated_before: Annotated[
        str | None,
        Field(description="Optional exclusive upper bound for updated timestamp/date."),
    ] = None,
    last_days: Annotated[
        int | None,
        Field(description="Optional shorthand timeframe (e.g. 7 = updated in last 7 days)."),
    ] = None,
    status: Annotated[str | None, Field(description="Optional ticket status filter (open, pending, solved, etc.).")] = None,
    include_solved: Annotated[
        bool,
        Field(description="Include solved/closed tickets when status is not explicitly provided."),
    ] = False,
    exclude_internal: Annotated[
        bool,
        Field(description="Exclude tickets tagged internal from search results."),
    ] = False,
    comment_author: Annotated[
        str | None,
        Field(description="Optional comment author filter (name/email/id), e.g. Tom."),
    ] = None,
) -> SearchTicketsByTextResult:
    normalized_updated_since = updated_since
    if last_days is not None:
        normalized_updated_since = (datetime.now(timezone.utc) - timedelta(days=int(last_days))).replace(microsecond=0).isoformat()

    result = zendesk_client.search_tickets_by_text(
        phrase=phrase,
        page=page,
        per_page=per_page,
        sort_by=sort_by,
        sort_order=sort_order,
        organization=organization,
        updated_since=normalized_updated_since,
        updated_before=updated_before,
        status=status,
        include_solved=include_solved,
        exclude_internal=exclude_internal,
        comment_author=comment_author,
    )
    return SearchTicketsByTextResult.model_validate(result)


@mcp.tool(
    name="sample_solved_tickets_for_agent",
    description="Return a random lightweight sample of solved tickets for an agent within a solved date range",
    structured_output=True,
)
def sample_solved_tickets_for_agent(
    agent: Annotated[str, Field(description="Agent assignee filter. Can be agent id, email, or name.")],
    solved_after: Annotated[str, Field(description="Inclusive lower bound date for solved tickets, e.g. 2026-02-01.")],
    solved_before: Annotated[str, Field(description="Exclusive upper bound date for solved tickets, e.g. 2026-03-01.")],
    count: Annotated[int, Field(description="How many random tickets to return.")] = 4,
    exclude_api_created: Annotated[
        bool,
        Field(description="Exclude tickets whose Zendesk via.channel is api."),
    ] = False,
    seed: Annotated[int | None, Field(description="Optional random seed for repeatable sampling.")] = None,
    max_pool: Annotated[int, Field(description="Maximum number of matching tickets to retrieve before sampling.")] = 250,
) -> RandomTicketSampleResult:
    search_result = zendesk_client.search_solved_tickets_for_agent(
        agent=agent,
        solved_after=solved_after,
        solved_before=solved_before,
        max_results=max_pool,
        exclude_api_created=exclude_api_created,
    )

    tickets = search_result["tickets"]
    sample_size = min(max(count, 1), len(tickets))
    rng = random.Random(seed)
    sampled_tickets = rng.sample(tickets, sample_size) if sample_size else []

    return RandomTicketSampleResult.model_validate(
        {
            "tickets": sampled_tickets,
            "requested_count": count,
            "sampled_count": len(sampled_tickets),
            "total_matches": search_result["total_matches"],
            "retrieved_count": search_result["retrieved_count"],
            "truncated": search_result["truncated"],
            "exclude_api_created": exclude_api_created,
            "excluded_api_created_count": search_result["excluded_api_created_count"],
            "agent": agent,
            "solved_after": solved_after,
            "solved_before": solved_before,
            "seed": seed,
        }
    )


@mcp.tool(
    name="review_random_solved_tickets_for_agent",
    description="Sample solved tickets for an agent in a date range and return the full review packet for each sampled ticket",
    structured_output=True,
)
def review_random_solved_tickets_for_agent(
    agent: Annotated[str, Field(description="Agent assignee filter. Can be agent id, email, or name.")],
    solved_after: Annotated[str, Field(description="Inclusive lower bound date for solved tickets, e.g. 2026-02-01.")],
    solved_before: Annotated[str, Field(description="Exclusive upper bound date for solved tickets, e.g. 2026-03-01.")],
    count: Annotated[int, Field(description="How many random tickets to review.")] = 4,
    exclude_api_created: Annotated[
        bool,
        Field(description="Exclude tickets whose Zendesk via.channel is api."),
    ] = False,
    seed: Annotated[int | None, Field(description="Optional random seed for repeatable sampling.")] = None,
    max_pool: Annotated[int, Field(description="Maximum number of matching tickets to retrieve before sampling.")] = 250,
) -> RandomTicketReviewResult:
    sample_result = sample_solved_tickets_for_agent(
        agent=agent,
        solved_after=solved_after,
        solved_before=solved_before,
        count=count,
        exclude_api_created=exclude_api_created,
        seed=seed,
        max_pool=max_pool,
    )

    reviews = []
    for sampled_ticket in sample_result.tickets:
        ticket_id = sampled_ticket.id
        if ticket_id is None:
            continue
        reviews.append(
            {
                "ticket_id": ticket_id,
                "ticket": _prepare_ticket_payload(ticket_id),
                "comments": zendesk_client.get_ticket_comments(ticket_id),
            }
        )

    review_input = build_batch_ticket_review_input(
        reviews=reviews,
        rubric_template=TICKET_ANALYSIS_TEMPLATE,
    )

    return RandomTicketReviewResult.model_validate(
        {
            "sampled_ticket_ids": [review["ticket_id"] for review in reviews],
            "sampled_ticket_urls": [(_ticket_url(review["ticket_id"]) or "") for review in reviews],
            "sampled_ticket_links": [(_ticket_link(review["ticket_id"]) or "") for review in reviews],
            "sampled_count": len(reviews),
            "total_matches": sample_result.total_matches,
            "retrieved_count": sample_result.retrieved_count,
            "truncated": sample_result.truncated,
            "exclude_api_created": sample_result.exclude_api_created,
            "excluded_api_created_count": sample_result.excluded_api_created_count,
            "agent": sample_result.agent,
            "solved_after": sample_result.solved_after,
            "solved_before": sample_result.solved_before,
            "seed": sample_result.seed,
            "review_input": review_input,
        }
    )


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
