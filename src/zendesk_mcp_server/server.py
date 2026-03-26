import json
import logging
import os
import random
import re
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
EST_TIMEZONE = timezone(timedelta(hours=-5), name="EST")

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
- If the ticket is waiting for a customer response, the "Status With" field must be set to "Customer"; otherwise mark the review as invalid and explain the mismatch.

General evidence rule:
{attribution_guardrails}

When reviewing a title, return one line each and exactly:
Validation: VALID or INVALID
Reason: <brief explanation>
Suggested Title: <only if invalid>

Be consistent and concise.
If multiple tickets are reviewed, also include:
Summary: <count valid> valid, <count invalid> invalid
"""

REVIEW_SINGLE_TICKET_TEMPLATE = """
Use the ticket title review policy to review Zendesk ticket {ticket_link}.

Instructions:
- Fetch the ticket first.
- Evaluate only the ticket title unless other ticket details are needed to understand obvious ambiguity.
- Apply the review policy exactly.
- Return the result in the required format.
"""

TICKET_ANALYSIS_TEMPLATE = """
You are reviewing Zendesk ticket {ticket_link} for ticket QA.

Use only the ticket details and ticket comments as evidence. Do not infer or invent facts that are not explicitly present in the ticket data. If a milestone or detail cannot be found, write "Not found".

Review goals:
1. Summarize the ticket record and documented handling steps based only on available evidence.
2. Highlight documented gaps, delays, missing confirmations, or unclear ownership transitions in the ticket record.
3. Produce a useful ticket-level QA summary for operational follow-up.
4. Attribute actions, ownership, and escalation leadership only when explicitly supported by the ticket record.

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
4. Attachment Evidence
   Report each item on its own line:
   - Crash-related attachments available:
   - Stacktrace attachments:
   - Replication path video:
   - Other crash-related attachments:
   Use exact attachment filenames when available. Otherwise write "Not found".
5. Tom Tovar Comment Check
   Report each item on its own line:
   - Tom commented:
   - First Tom comment:
   - Latest Tom comment:
   - Tom comment summary:
   Use ticket metadata fields (tom_tovar_*) and comments as evidence.
6. Process Findings
   List concrete ticket-level observations about documented process completeness or gaps based on evidence from the ticket and comments.
7. Overall QA Summary
   Give a concise summary of the ticket record quality.
   Focus on documented strengths, missing evidence, and follow-up items.

Rules:
- Ticket Title is formated correctly.
- Escalated Tickets are tickets where the Escalation Status field is populated.
- Do not use external assumptions or general policy knowledge unless explicitly present in the ticket.
- Do not treat missing evidence as completed work.
- Evaluate the ticket record only, not any employee's overall performance.
- Do not score, rank, or otherwise evaluate a person.
- Follow the attribution guardrails exactly:
  {attribution_guardrails}
- Email chain and preamble scope: when a ticket originates from an email chain, use only support interaction evidence (agent public comments, customer replies in-ticket, and internal notes) to evaluate the ticket record.
- Do not use email chain preambles, introductory forwarding text, or prior forwarded email history to justify agent handling decisions.
- Customer context statements in the opening message (for example, "I am writing on behalf of X who is on leave") explain ticket origin only and must not be used to justify or excuse agent delay/timeliness.
- Delay justification rule: attribute a delay to a specific cause only when that cause is explicitly documented by the agent in their actions or internal notes.
- Customer-side context in the opening message is not documented delay justification for agent handling.
- If no explicit agent-documented delay reason exists, assess delay on its face based on the timeline evidence.
- Evidence source discipline: for each timeline item and process finding, explicitly state source and author (for example: agent public comment, customer reply, internal note, email chain preamble).
- Do not mix evidence sources when drawing ticket QA conclusions; customer context about their own situation cannot be used to evaluate or excuse agent actions.
- For Escalated Tickets, if the customer has not explicitly confirmed the solution worked, do not mark the resolution as customer-acknowledged.
- Crash/ANR ticket rule: if the ticket has tag "crash_detected" or "anr_yes", verify crash/ANR evidence handling.
- For crash_detected/anr_yes tickets, treat stacktrace evidence as present only when there is explicit stacktrace content in comments or a relevant crash attachment (for example .ips, .crash, .dmp, or filenames that explicitly indicate a crash log/stacktrace).
- If a crash_detected/anr_yes ticket has no stacktrace evidence, verify the assigned support engineer asked the customer for stacktrace/crash log details. If no such request appears in comments, flag this as a process gap.
- For crash_detected/anr_yes tickets, enforce stacktrace request timeliness: if stacktrace evidence is not already present, the first explicit support request for stacktrace/crash logs should occur within 1 hour of crash identification.
- For this check, infer crash identification time from the earliest explicit crash evidence in the ticket/comments; if the ticket already has tag "crash_detected" or "anr_yes", use ticket created timestamp when no earlier signal is available.
- If the first stacktrace request is more than 1 hour after crash identification, explicitly flag "Late stacktrace request (>1h)" in Process Findings.
- For crash_detected/anr_yes tickets, always calculate and report "Time to escalation from ticket creation" using ticket created timestamp and the first explicit escalation timestamp in the evidence.
- If escalation evidence exists but no escalation timestamp can be determined, write "Not found" and explicitly flag this as a process gap.
- For crash_detected/anr_yes tickets, if there is evidence of a crash/ANR but the review does not explicitly identify crash/ANR handling in Timeline/Process Findings, explicitly call that out as a critical ticket QA gap.
- For crash_detected/anr_yes tickets, if there is no stacktrace evidence and no explicit stacktrace/crash-log request, explicitly call that out as a critical ticket QA gap.
- For crash_detected/anr_yes tickets, if the first stacktrace/crash-log request is more than 1 hour after crash identification, explicitly call that out as a critical ticket QA gap.
- For crash_detected/anr_yes tickets, escalation must be timely: if first escalation occurs more than 1 hour after crash identification (or cannot be verified due to missing timestamp), explicitly call that out as a critical ticket QA gap.
- Before producing the final review, verify that every named person's role or responsibility is directly supported by ticket evidence; remove or soften any claim that depends on inference.
- Prefer concise, evidence-based statements.
"""

COMMENT_DRAFT_TEMPLATE = """
You are a helpful Zendesk support agent. You need to draft a response to ticket {ticket_link}.

Please fetch the ticket info, comments and knowledge base to draft a professional and helpful response that:
1. Acknowledges the customer's concern
2. Addresses the specific issues raised
3. Provides clear next steps or ask for specific details need to proceed
4. Maintains a friendly and professional tone
5. Ask for confirmation before commenting on the ticket

Apply these evidence and attribution guardrails:
{attribution_guardrails}

The response should be formatted well and ready to be posted as a comment.
"""

ticket_field_option_resolver = TicketFieldOptionResolver(zendesk_client)
ticket_field_option_resolver.load()


def _prepare_ticket_payload(ticket_id: int) -> dict[str, Any]:
    ticket = zendesk_client.get_ticket(ticket_id)
    ticket = apply_ticket_field_displays(ticket, ticket_field_option_resolver)
    ticket["ticket_url"] = _ticket_url(ticket_id)
    ticket["ticket_link"] = _ticket_link(ticket_id)
    _hydrate_ticket_user_fields(ticket)
    return ticket


def _hydrate_ticket_user_fields(ticket: dict[str, Any]) -> None:
    requester_id = ticket.get("requester_id")
    assignee_id = ticket.get("assignee_id")
    user_ids = [user_id for user_id in (requester_id, assignee_id) if user_id is not None]
    if not user_ids:
        return

    try:
        users_by_id = zendesk_client.get_users_by_ids(user_ids)
    except Exception as exc:
        logger.warning("Failed to hydrate ticket users for ticket %s: %s", ticket.get("id"), exc)
        return

    requester = users_by_id.get(int(requester_id)) if requester_id is not None else None
    assignee = users_by_id.get(int(assignee_id)) if assignee_id is not None else None

    ticket["requester"] = requester
    ticket["assignee"] = assignee
    ticket["requester_name"] = requester.get("name") if requester else None
    ticket["requester_email"] = requester.get("email") if requester else None
    ticket["assignee_name"] = assignee.get("name") if assignee else None
    ticket["assignee_email"] = assignee.get("email") if assignee else None


def _hydrate_comment_author_fields(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    author_ids = sorted(
        {int(comment["author_id"]) for comment in comments if comment.get("author_id") is not None},
    )
    if not author_ids:
        return comments

    try:
        users_by_id = zendesk_client.get_users_by_ids(author_ids)
    except Exception as exc:
        logger.warning("Failed to hydrate comment authors: %s", exc)
        return comments

    for comment in comments:
        author_id = comment.get("author_id")
        user = users_by_id.get(int(author_id)) if author_id is not None else None
        comment["author"] = user
        comment["author_name"] = user.get("name") if user else None
        comment["author_email"] = user.get("email") if user else None
    return comments


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
        dt = dt.astimezone(EST_TIMEZONE)
        return dt.strftime("%B %-d, %Y at %H:%M EST")
    except ValueError:
        return value


def _build_ticket_summary(ticket: dict[str, Any]) -> str:
    custom_fields = ticket.get("custom_fields", {})
    ticket_id = ticket.get("id")
    ticket_link = _ticket_link(ticket_id) or f"#{ticket_id}"
    is_feature_request = _is_feature_request_ticket(ticket.get("subject"))
    production_impact = (
        ProductionImpactAssessment()
        if is_feature_request
        else _build_production_impact_assessment(ticket=ticket, comments=[])
    )
    display_priority = "low" if is_feature_request else ticket.get("priority", "N/A")
    lines = [
        f"# Ticket {ticket_link} - {ticket.get('subject', 'Untitled')}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Subject | {ticket.get('subject', 'N/A')} |",
        f"| Status | {ticket.get('status', 'N/A')} |",
        f"| Priority | {display_priority} |",
        f"| Production Issue | {'Yes' if production_impact.is_production_issue else 'No'} |",
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
    match_type: str | None = None


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


class UserItem(BaseModel):
    id: int
    name: str | None = None
    email: str | None = None
    active: bool | None = None
    role: str | None = None
    organization_id: int | None = None
    external_id: str | None = None


class SearchUsersResult(BaseModel):
    users: list[UserItem]
    count: int
    query: str
    page: int
    per_page: int
    has_more: bool
    next_page: int | None = None
    previous_page: int | None = None


class TranslateUsersResult(BaseModel):
    users_by_id: dict[str, UserItem]
    missing_ids: list[int] = Field(default_factory=list)


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
    exact_query: str
    partial_query: str | None = None
    search_mode: str = "exact"
    exact_count: int = 0
    partial_fallback_used: bool = False
    partial_fallback_reason: str | None = None
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
    production_ticket_ids: list[int] = Field(default_factory=list)
    production_ticket_links: list[str] = Field(default_factory=list)
    production_ticket_count: int = 0
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


class CrashAttachmentSignal(BaseModel):
    file_name: str
    evidence_type: str
    source: str
    content_type: str | None = None
    size: int | None = None


class CrashAttachmentSummary(BaseModel):
    has_crash_related_attachments: bool = False
    has_stacktrace_attachment: bool = False
    has_replication_video: bool = False
    stacktrace_files: list[str] = Field(default_factory=list)
    replication_videos: list[str] = Field(default_factory=list)
    crash_related_files: list[str] = Field(default_factory=list)
    signals: list[CrashAttachmentSignal] = Field(default_factory=list)


class ProductionImpactAssessment(BaseModel):
    is_production_issue: bool = False
    evidence: list[str] = Field(default_factory=list)
    non_production_signals: list[str] = Field(default_factory=list)


class TicketTroubleAssessment(BaseModel):
    ticket_id: int
    ticket_url: str
    ticket_link: str
    subject: str | None = None
    status: str | None = None
    priority: str | None = None
    is_escalated: bool = False
    priority_interpretation: str | None = None
    in_trouble: bool
    risk_score: int
    flags: list[TicketTroubleFlag]
    crash_attachment_summary: CrashAttachmentSummary | None = None
    production_impact: ProductionImpactAssessment = Field(default_factory=ProductionImpactAssessment)
    recent_comment_notes: list[str] = Field(default_factory=list)
    tom: str = "☐"
    tom_tovar_commented: bool = False
    tom_tovar_comment_marker: str | None = None
    tom_tovar_comment_count: int = 0
    tom_tovar_latest_comment_at: str | None = None
    tom_tovar_comment_summary: str | None = None


class ScanTicketsInTroubleResult(BaseModel):
    created_last_hours: int
    scanned_count: int
    in_trouble_count: int
    ticket_list_markdown: str = ""
    tickets: list[TicketTroubleAssessment]


class ScanCrashTicketsInTroubleResult(BaseModel):
    tag: str
    scanned_count: int
    in_trouble_count: int
    total_matches: int
    retrieved_count: int
    truncated: bool
    ticket_list_markdown: str = ""
    tickets: list[TicketTroubleAssessment]


class ImportantTodayFilters(BaseModel):
    agent: str | None = None
    organization: str | None = None
    recent_activity_hours: int
    stale_hours: int
    exclude_internal: bool = True


class GetImportantTicketsTodayResult(BaseModel):
    filters: ImportantTodayFilters
    candidate_count: int
    in_trouble_count: int
    ticket_list_markdown: str = ""
    tickets: list[TicketTroubleAssessment]


DEFAULT_INITIAL_RESPONSE_SLA_MINUTES = 60
DEFAULT_HIGH_PRIORITY_STALE_HOURS = 8
PENDING_TICKET_PRIORITY_DISCOUNT = 15

TROUBLE_FLAG_WEIGHTS: dict[str, int] = {
    "crash_tag_missing_unreviewed_attachment_evidence": 100,
    "missing_initial_response": 34,
    "crash_process_gap": 45,
    "crash_tag_missing": 50,
    "internal_tag_title_mismatch": 18,
    "customer_urgency": 51,
    "customer_unhappy": 52,
    "customer_repeated_pressure": 58,
    "ticket_report_request": 32,
    "meeting_summary_missing": 28,
    "status_fields_incomplete": 24,
    "customer_comment_no_response": 30,
    "production_customer_comment_no_response": 56,
    "sev1_customer_data_follow_up_overdue": 38,
    "solved_without_customer_confirmation": 10,
    "high_priority_no_recent_updates": 25,
    "support_owned_no_recent_updates": 25,
    "late_initial_response": 20,
    "late_stacktrace_request": 44,
    "title_incorrect": 45,
    "production_user_impact": 35,
}
SEVERITY_FALLBACK_WEIGHTS = {"high": 30, "medium": 15, "low": 5}
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
CUSTOMER_FOLLOW_UP_SLA_HOURS = 4
PRODUCTION_CUSTOMER_FOLLOW_UP_SLA_HOURS = 2
SEV1_CUSTOMER_DATA_FOLLOW_UP_SLA_HOURS = 1
NO_RESPONSE_EXPECTED_OPEN_STALE_DAYS = 5
OPEN_TICKET_STATUSES = {"new", "open", "pending", "hold", "on-hold"}
CRASH_SIGNAL_TERMS = [
    "crash",
    "crashed",
    "crashing",
    "force close",
    "force-close",
    "unexpectedly close",
    "unexpectedly closes",
    "unexpectedly closed",
    "fatal exception",
    "segmentation fault",
]
MEETING_REFERENCE_PATTERN = re.compile(
    r"\b(?:phone call|conference call|zoom(?: meeting)?|google meet|teams(?: meeting)?|webex|meeting|screen ?share|screenshare)\b",
    re.IGNORECASE,
)
MEETING_REQUEST_OR_SCHEDULE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:schedule|scheduled|scheduling|reschedule|rescheduled|rescheduling)\b.{0,25}"
        r"\b(?:a\s+|the\s+)?(?:phone call|conference call|call|zoom(?: meeting)?|google meet|teams(?: meeting)?|webex|meeting|screen ?share|screenshare)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:let'?s|can we|could we|should we|please|want to|would like to|able to)\b.{0,25}"
        r"\b(?:schedule|set up|arrange|book|have|join|jump on|do)\b.{0,20}"
        r"\b(?:a\s+|the\s+)?(?:phone call|conference call|call|zoom(?: meeting)?|google meet|teams(?: meeting)?|webex|meeting|screen ?share|screenshare)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:jump on|join|have|set up|arrange|book|do)\b.{0,20}"
        r"\b(?:a\s+|the\s+)?(?:phone call|conference call|call|zoom(?: meeting)?|google meet|teams(?: meeting)?|webex|meeting|screen ?share|screenshare)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:phone call|conference call|call|zoom(?: meeting)?|google meet|teams(?: meeting)?|webex|meeting)\b.{0,20}"
        r"\b(?:confirmed|booked|scheduled|arranged|requested)\b",
        re.IGNORECASE | re.DOTALL,
    ),
)
MEETING_SUMMARY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:call summary|meeting summary|call notes|meeting notes)\b", re.IGNORECASE),
    re.compile(r"\b(?:after|following)\s+(?:our|the)\s+(?:call|meeting)\b", re.IGNORECASE),
    re.compile(r"\bas discussed\s+(?:on|during|in)\s+(?:our|the)\s+(?:call|meeting)\b", re.IGNORECASE),
    re.compile(r"\b(?:on|during)\s+(?:our|the)\s+(?:call|meeting)\b", re.IGNORECASE),
    re.compile(r"\brecap\s+(?:from|of)\s+(?:our|the)\s+(?:call|meeting)\b", re.IGNORECASE),
)
MEETING_CONTEXT_TIME_PATTERN = re.compile(
    r"\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}\s*(?:am|pm)?)\b",
    re.IGNORECASE,
)
DATE_OR_TIME_PATTERN = re.compile(
    r"\b("
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"\d{1,2}/\d{1,2}(?:/\d{2,4})?"
    r"|"
    r"\d{1,2}:\d{2}\s?(?:am|pm)?"
    r"|"
    r"(?:mon|tue|wed|thu|fri|sat|sun)(?:day)?"
    r"|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)(?:[a-z]*)\s+\d{1,2}(?:,\s*\d{4})?"
    r")\b",
    re.IGNORECASE,
)
MEETING_DATETIME_PATTERN = re.compile(
    r"\b(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
    r"(?:\D{0,12}(?P<time>\d{1,2}:\d{2})(?:\s*(?P<ampm>am|pm))?)?\b",
    re.IGNORECASE,
)
CRASH_ATTACHMENT_KEYWORDS = (
    "crash",
    "stacktrace",
    "stack_trace",
    "stack-trace",
    "stack",
    "backtrace",
    "exception",
    "fatal",
    "anr",
    "deobfuscat",
    "deobfuscated",
)
VIDEO_ATTACHMENT_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".webm", ".mkv")
IMAGE_ATTACHMENT_EXTENSIONS = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".bmp", ".webp")
TOM_TOVAR_USER_ID = 4293579406
TOM_TOVAR_COMMENT_MARKER = "⚠️ Tom Tovar (id=4293579406) commented on this ticket."
PRODUCTION_SIGNAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bprod(?:uction)?\b", re.IGNORECASE), "Mentions production environment."),
    (
        re.compile(r"\b(?:app\s*store|appstore|play\s*store|google play)\b", re.IGNORECASE),
        "References a live store release.",
    ),
    (re.compile(r"\b(?:already\s+)?live\b", re.IGNORECASE), "Mentions the app is live."),
    (re.compile(r"\bend[ -]?users?\b", re.IGNORECASE), "Mentions end-user impact."),
    (
        re.compile(r"\b(?:users?|customers?)\s+(?:are\s+)?(?:impacted|affected|blocked)\b", re.IGNORECASE),
        "States that users/customers are impacted.",
    ),
    (
        re.compile(r"\bimpact(?:ing|ed)?\s+(?:our\s+)?(?:users?|customers?)\b", re.IGNORECASE),
        "States that users/customers are being impacted.",
    ),
)
NON_PRODUCTION_SIGNAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\buat\b", re.IGNORECASE), "Mentions UAT."),
    (re.compile(r"\bdev(?:elopment|eng)?\b", re.IGNORECASE), "Mentions DEV/engineering environment."),
    (re.compile(r"\bqa\b", re.IGNORECASE), "Mentions QA environment."),
    (re.compile(r"\bstaging\b", re.IGNORECASE), "Mentions staging environment."),
    (re.compile(r"\bsandbox\b", re.IGNORECASE), "Mentions sandbox environment."),
    (re.compile(r"\btesting\b", re.IGNORECASE), "Mentions testing environment."),
    (re.compile(r"\btestflight\b", re.IGNORECASE), "Mentions TestFlight."),
    (re.compile(r"\bpre[- ]?release\b", re.IGNORECASE), "Mentions pre-release environment."),
    (re.compile(r"\bnon[- ]?prod(?:uction)?\b", re.IGNORECASE), "Mentions non-production environment."),
    (re.compile(r"\binternal build\b", re.IGNORECASE), "Mentions internal build."),
)
ISSUE_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bissue\b", re.IGNORECASE),
    re.compile(r"\bproblem\b", re.IGNORECASE),
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"\bbug\b", re.IGNORECASE),
    re.compile(r"\bcrash(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\banr\b", re.IGNORECASE),
    re.compile(r"\bfail(?:ed|ing|ure)?\b", re.IGNORECASE),
    re.compile(r"\bnot\s+work(?:ing)?\b", re.IGNORECASE),
    re.compile(r"\bdoes(?:\s+not|\s*n't)\s+work\b", re.IGNORECASE),
    re.compile(r"\bunable\b", re.IGNORECASE),
    re.compile(r"\bcannot\b", re.IGNORECASE),
    re.compile(r"\bcan't\b", re.IGNORECASE),
    re.compile(r"\bblocked\b", re.IGNORECASE),
)
TRAINING_REQUEST_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\btraining\b", re.IGNORECASE),
    re.compile(r"\bsession\b", re.IGNORECASE),
    re.compile(r"\bwalkthrough\b", re.IGNORECASE),
    re.compile(r"\bdemo\b", re.IGNORECASE),
    re.compile(r"\blearn\b", re.IGNORECASE),
    re.compile(r"\bunderstand\b", re.IGNORECASE),
    re.compile(r"\breview\b", re.IGNORECASE),
    re.compile(r"\btelemetry\b", re.IGNORECASE),
)
TICKET_REPORT_REQUEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bticket\s+report\b", re.IGNORECASE),
    re.compile(r"\breport\s+of\s+tickets\b", re.IGNORECASE),
    re.compile(r"\breport\s+for\s+tickets\b", re.IGNORECASE),
    re.compile(r"\breport\s+about\s+tickets\b", re.IGNORECASE),
    re.compile(r"\breporte\s+de\s+tickets\b", re.IGNORECASE),
    re.compile(r"\binforme\s+de\s+tickets\b", re.IGNORECASE),
    re.compile(r"\bzendesk\s+ticket\s+report\b", re.IGNORECASE),
    re.compile(r"\breporte\s+de\s+tickets\s+de\s+zendesk\b", re.IGNORECASE),
)
CUSTOMER_URGENCY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\burgent\b", re.IGNORECASE),
    re.compile(r"\burgency\b", re.IGNORECASE),
    re.compile(r"\bhigh[ -]?priority\b", re.IGNORECASE),
    re.compile(r"\btop[ -]?priority\b", re.IGNORECASE),
    re.compile(r"\bpriority\s+(?:issue|request|case|ticket|matter)\b", re.IGNORECASE),
    re.compile(r"\basap\b", re.IGNORECASE),
    re.compile(r"\bas soon as possible\b", re.IGNORECASE),
    re.compile(r"\bimmediate(?:ly)?\b", re.IGNORECASE),
    re.compile(r"\btime[- ]sensitive\b", re.IGNORECASE),
    re.compile(r"\bcritical\b", re.IGNORECASE),
    re.compile(r"\bfast[- ]?track(?:ing|ed)?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:go[- ]?live|launch(?:ing|es|ed)?)\b.{0,24}\b(?:today|tomorrow)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:today|tomorrow)\b.{0,24}\b(?:go[- ]?live|launch(?:ing|es|ed)?)\b",
        re.IGNORECASE | re.DOTALL,
    ),
)
UNHAPPY_COMMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bnot\s+happy\b", re.IGNORECASE),
    re.compile(r"\bunhappy\b", re.IGNORECASE),
    re.compile(r"\bfrustrat(?:ed|ing|ion)?\b", re.IGNORECASE),
    re.compile(r"\bupset\b", re.IGNORECASE),
    re.compile(r"\bangry\b", re.IGNORECASE),
    re.compile(r"\bdissatisfied\b", re.IGNORECASE),
    re.compile(r"\bdisappointed\b", re.IGNORECASE),
    re.compile(r"\bannoy(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bunacceptable\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:one|body)\s+has\s+(?:responded|replied|gotten\s+back)\b", re.IGNORECASE),
    re.compile(r"\bno\s+response\b", re.IGNORECASE),
    re.compile(r"\bstill\s+waiting\b", re.IGNORECASE),
    re.compile(r"\bwaiting\s+for\s+(?:an?\s+)?(?:response|reply|update)\b", re.IGNORECASE),
    re.compile(r"\bany\s+update\b", re.IGNORECASE),
    re.compile(r"\bfollow(?:ing)?\s+up\b", re.IGNORECASE),
)
CUSTOMER_DATA_REQUEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:please|kindly|can you|could you|would you|when you can)\b.{0,25}"
        r"\b(?:provide|share|send|attach|upload|include|confirm|let us know)\b.{0,40}"
        r"\b(?:log|logs|stacktrace|stack trace|crash log|trace|details|detail|information|info|"
        r"screenshot|screen recording|recording|video|steps|steps to reproduce|repro|sample app|"
        r"sample project|apk|ipa|build|version|file|files|data)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:need|needed|awaiting|waiting\s+for|looking\s+for)\b.{0,30}"
        r"\b(?:more|additional|the)\b.{0,20}"
        r"\b(?:details|information|info|data|logs|log|stacktrace|stack trace|screenshot|video|"
        r"steps|repro|sample app|sample project|apk|ipa|build|version)\b",
        re.IGNORECASE | re.DOTALL,
    ),
)
CUSTOMER_DATA_PROVIDED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:attached|attachment|upload(?:ed)?|shared|included|below|here(?:'s| is)|sending)\b.{0,30}"
        r"\b(?:log|logs|stacktrace|stack trace|crash log|details|information|info|screenshot|"
        r"screen recording|recording|video|steps|steps to reproduce|repro|sample app|sample project|"
        r"apk|ipa|build|version|file|files|data)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:app version|os version|build number|build id|package name|bundle id|steps to reproduce)\b",
        re.IGNORECASE,
    ),
)
CUSTOMER_UPDATE_REQUEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bany\s+update\b", re.IGNORECASE),
    re.compile(r"\b(?:regular|frequent)\s+updates?\b", re.IGNORECASE),
    re.compile(r"\bkeep\s+us\s+updated\b", re.IGNORECASE),
    re.compile(r"\bupdate\s+us\b", re.IGNORECASE),
    re.compile(r"\bhourly\s+updates?\b", re.IGNORECASE),
    re.compile(r"\bevery\s+hour\b", re.IGNORECASE),
    re.compile(r"\bfollow(?:ing)?\s+up\b", re.IGNORECASE),
    re.compile(r"\bstill\s+waiting\b", re.IGNORECASE),
    re.compile(r"\bwaiting\s+for\s+(?:an?\s+)?(?:update|response|reply)\b", re.IGNORECASE),
)
CUSTOMER_MEETING_REQUEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:can\s+we|could\s+we|let'?s|please|need\s+to|want\s+to|would\s+like\s+to)\b.{0,30}"
        r"\b(?:schedule|set up|arrange|book|have|join|jump on|do)\b.{0,20}"
        r"\b(?:a\s+|the\s+)?(?:call|meeting|zoom(?: meeting)?|google meet|teams(?: meeting)?|webex)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:request(?:ing)?|asking\s+for)\b.{0,20}\b(?:a\s+|the\s+)?(?:call|meeting|zoom(?: meeting)?)\b",
        re.IGNORECASE | re.DOTALL,
    ),
)


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


def _append_unique(items: list[str], value: str | None) -> None:
    if value and value not in items:
        items.append(value)


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", " ", text)


def _comment_text(comment: dict[str, Any]) -> str:
    return " ".join(
        filter(
            None,
            [
                str(comment.get("body") or ""),
                _strip_html(comment.get("html_body")),
            ],
        )
    )


def _comment_snippet(comment: dict[str, Any], limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", _comment_text(comment)).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _normalize_priority_value(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", " ").replace("_", " ")


def _is_sev1_priority_value(value: Any) -> bool:
    normalized = _normalize_priority_value(value)
    if not normalized:
        return False
    return normalized in {"urgent", "sev1", "sev 1", "p1", "priority 1", "priority1", "severity 1", "severity1"}


def _is_sev1_ticket(ticket: dict[str, Any], is_escalated: bool) -> bool:
    if not is_escalated:
        return False

    custom_fields = ticket.get("custom_fields") if isinstance(ticket.get("custom_fields"), dict) else {}
    priority_candidates = [
        ticket.get("priority"),
        custom_fields.get("Priority"),
        custom_fields.get("Eng Priority"),
    ]
    return any(_is_sev1_priority_value(candidate) for candidate in priority_candidates)


def _comment_requests_customer_data(comment: dict[str, Any]) -> bool:
    text = _comment_text(comment)
    return any(pattern.search(text) for pattern in CUSTOMER_DATA_REQUEST_PATTERNS)


def _customer_comment_provides_requested_data(comment: dict[str, Any]) -> bool:
    attachments = comment.get("attachments") or []
    if attachments:
        return True

    text = _comment_text(comment)
    if any(pattern.search(text) for pattern in CUSTOMER_DATA_PROVIDED_PATTERNS):
        return True

    if _contains_any(text, ["exception", "stacktrace", "stack trace", "logcat", "fatal exception", "traceback"]):
        return True

    return False


def _contains_call_mention(text: str | None) -> bool:
    if not text:
        return False
    if any(pattern.search(text) for pattern in MEETING_REQUEST_OR_SCHEDULE_PATTERNS):
        return True
    return bool(
        MEETING_REFERENCE_PATTERN.search(text)
        and MEETING_CONTEXT_TIME_PATTERN.search(text)
    )


def _classify_meeting_reference(text: str | None) -> tuple[bool, datetime | None]:
    if not text:
        return False, None

    if not _contains_call_mention(text):
        return False, None

    scheduled_at = _extract_meeting_scheduled_at(text)
    return True, scheduled_at


def _build_customer_unhappy_flag(
    comments: list[dict[str, Any]],
    requester_id: int | None,
) -> TicketTroubleFlag | None:
    for comment in sorted(
        comments,
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    ):
        text = _comment_text(comment)
        if not text:
            continue
        for pattern in UNHAPPY_COMMENT_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            source = _comment_source(comment, requester_id)
            created_at = comment.get("created_at") or "unknown time"
            return TicketTroubleFlag(
                code="customer_unhappy",
                severity="high",
                message=(
                    "Comment suggests customer dissatisfaction; treat this as a high-priority item. "
                    f"Evidence: '{match.group(0)}' in {source} at {created_at}. "
                    f"Comment: \"{_comment_snippet(comment)}\""
                ),
            )
    return None


def _build_customer_comment_response_flag(
    comments: list[dict[str, Any]],
    requester_id: int | None,
    updated_at: datetime | None,
    status: str | None,
    production_impact: ProductionImpactAssessment,
) -> TicketTroubleFlag | None:
    if requester_id is None:
        return None

    public_comments_sorted = sorted(
        [comment for comment in comments if comment.get("public")],
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    if not public_comments_sorted:
        return None

    reference_time = updated_at or datetime.now(timezone.utc)
    follow_up_sla_hours = (
        PRODUCTION_CUSTOMER_FOLLOW_UP_SLA_HOURS
        if production_impact.is_production_issue
        else CUSTOMER_FOLLOW_UP_SLA_HOURS
    )
    follow_up_deadline = timedelta(hours=follow_up_sla_hours)

    customer_public_comments = [
        comment for comment in public_comments_sorted if comment.get("author_id") == requester_id
    ]
    for customer_comment in customer_public_comments:
        customer_time = _parse_iso_datetime(customer_comment.get("created_at"))
        if customer_time is None:
            continue

        if _is_no_response_expected_comment(customer_comment):
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
                return TicketTroubleFlag(
                    code="customer_comment_no_response",
                    severity="high",
                    message=(
                        "Ticket stayed open more than "
                        f"{NO_RESPONSE_EXPECTED_OPEN_STALE_DAYS} days after a no-response-expected "
                        "customer update, with no later public comments."
                    ),
                )
            continue

        first_follow_up_after_customer: datetime | None = None
        for possible_reply in public_comments_sorted:
            reply_time = _parse_iso_datetime(possible_reply.get("created_at"))
            if reply_time is None or reply_time <= customer_time:
                continue
            if possible_reply.get("author_id") == requester_id:
                continue
            first_follow_up_after_customer = reply_time
            break

        response_delay = (
            first_follow_up_after_customer - customer_time
            if first_follow_up_after_customer is not None
            else reference_time - customer_time
        )
        if response_delay <= follow_up_deadline:
            continue

        delay_hours = max(int(response_delay.total_seconds() // 3600), 1)
        snippet = _comment_snippet(customer_comment)
        if production_impact.is_production_issue:
            return TicketTroubleFlag(
                code="production_customer_comment_no_response",
                severity="high",
                message=(
                    "Production-impact customer comment is still waiting on a public Appdome response. "
                    f"No public reply within {follow_up_sla_hours}h; waiting about {delay_hours}h. "
                    f"Comment: \"{snippet}\""
                ),
            )

        return TicketTroubleFlag(
            code="customer_comment_no_response",
            severity="high",
            message=(
                "Customer public comment did not receive a public Appdome response "
                f"within {follow_up_sla_hours}h; waiting about {delay_hours}h. "
                f"Comment: \"{snippet}\""
            ),
        )

    return None


def _customer_comment_matches_any(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            matches.append(match.group(0))
    return matches


def _build_customer_repeated_pressure_flag(
    comments: list[dict[str, Any]],
    requester_id: int | None,
) -> TicketTroubleFlag | None:
    if requester_id is None:
        return None

    pressure_comments: list[dict[str, Any]] = []
    categories: set[str] = set()

    for comment in sorted(
        comments,
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    ):
        if not bool(comment.get("public")) or comment.get("author_id") != requester_id:
            continue
        text = _comment_text(comment)
        if not text:
            continue

        matched = False
        if _customer_comment_matches_any(text, UNHAPPY_COMMENT_PATTERNS):
            categories.add("dissatisfaction/frustration")
            matched = True
        if _customer_comment_matches_any(text, CUSTOMER_UPDATE_REQUEST_PATTERNS):
            categories.add("repeat update requests")
            matched = True
        if _customer_comment_matches_any(text, CUSTOMER_MEETING_REQUEST_PATTERNS) or _contains_call_mention(text):
            categories.add("meeting/call requests")
            matched = True
        if matched:
            pressure_comments.append(comment)

    if len(pressure_comments) < 2:
        return None

    first_at = pressure_comments[0].get("created_at") or "unknown time"
    latest = pressure_comments[-1]
    latest_at = latest.get("created_at") or "unknown time"
    category_summary = ", ".join(sorted(categories))
    return TicketTroubleFlag(
        code="customer_repeated_pressure",
        severity="high",
        message=(
            "Customer posted multiple pressure/escalation comments on the ticket. "
            f"Detected {len(pressure_comments)} customer comments with {category_summary}. "
            f"Window: {first_at} -> {latest_at}. "
            f"Latest comment: \"{_comment_snippet(latest)}\""
        ),
    )


def _build_customer_urgency_flag(
    ticket: dict[str, Any],
    comments: list[dict[str, Any]],
    requester_id: int | None,
) -> TicketTroubleFlag | None:
    subject = str(ticket.get("subject") or "")
    description = str(ticket.get("description") or "")

    for field_name, text in (("subject", subject), ("description", description)):
        for pattern in CUSTOMER_URGENCY_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            return TicketTroubleFlag(
                code="customer_urgency",
                severity="high",
                message=(
                    "Customer language marks this ticket as urgent/high-priority; highlight in scan results. "
                    f"Evidence: '{match.group(0)}' in ticket {field_name}."
                ),
            )

    for comment in sorted(
        comments,
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    ):
        if requester_id is not None and comment.get("author_id") != requester_id:
            continue
        text = _comment_text(comment)
        if not text:
            continue
        for pattern in CUSTOMER_URGENCY_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            created_at = comment.get("created_at") or "unknown time"
            return TicketTroubleFlag(
                code="customer_urgency",
                severity="high",
                message=(
                    "Customer explicitly marked this issue as urgent/high-priority; highlight in scan results. "
                    f"Evidence: '{match.group(0)}' in customer_public_comment at {created_at}."
                ),
            )

    return None


def _build_ticket_report_request_flag(
    ticket: dict[str, Any],
    comments: list[dict[str, Any]],
    requester_id: int | None,
) -> TicketTroubleFlag | None:
    subject = str(ticket.get("subject") or "")
    description = str(ticket.get("description") or "")

    for field_name, text in (("subject", subject), ("description", description)):
        for pattern in TICKET_REPORT_REQUEST_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            return TicketTroubleFlag(
                code="ticket_report_request",
                severity="medium",
                message=(
                    "Ticket includes an explicit Zendesk ticket-report request; treat this as an elevated-attention "
                    f"signal. Evidence: '{match.group(0)}' in ticket {field_name}."
                ),
            )

    for comment in sorted(
        comments,
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    ):
        if requester_id is not None and comment.get("author_id") != requester_id:
            continue
        text = _comment_text(comment)
        if not text:
            continue
        for pattern in TICKET_REPORT_REQUEST_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            created_at = comment.get("created_at") or "unknown time"
            return TicketTroubleFlag(
                code="ticket_report_request",
                severity="medium",
                message=(
                    "Customer requested a Zendesk ticket report; treat this as an elevated-attention signal. "
                    f"Evidence: '{match.group(0)}' in customer_public_comment at {created_at}."
                ),
            )

    return None


def _collect_environment_signal_matches(
    text: str | None,
    patterns: tuple[tuple[re.Pattern[str], str], ...],
    source: str,
) -> list[str]:
    if not text:
        return []
    matches: list[str] = []
    for pattern, label in patterns:
        if pattern.search(text):
            matches.append(f"{label} ({source}).")
    return matches


def _contains_pattern_match(text: str | None, patterns: tuple[re.Pattern[str], ...]) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in patterns)


def _build_production_impact_assessment(
    ticket: dict[str, Any],
    comments: list[dict[str, Any]],
) -> ProductionImpactAssessment:
    evidence: list[str] = []
    non_production_signals: list[str] = []
    has_issue_signal = False
    has_training_request_signal = False
    custom_fields = ticket.get("custom_fields") if isinstance(ticket.get("custom_fields"), dict) else {}

    release_stage = custom_fields.get("Release Stage")
    support_class = custom_fields.get("Support Class")
    subject = ticket.get("subject")
    description = ticket.get("description")

    for match in _collect_environment_signal_matches(str(release_stage or ""), PRODUCTION_SIGNAL_PATTERNS, "release stage"):
        _append_unique(evidence, match)
    for match in _collect_environment_signal_matches(str(release_stage or ""), NON_PRODUCTION_SIGNAL_PATTERNS, "release stage"):
        _append_unique(non_production_signals, match)

    for match in _collect_environment_signal_matches(str(support_class or ""), NON_PRODUCTION_SIGNAL_PATTERNS, "support class"):
        _append_unique(non_production_signals, match)

    for field_name, text in (("subject", subject), ("description", description)):
        for match in _collect_environment_signal_matches(text, PRODUCTION_SIGNAL_PATTERNS, field_name):
            _append_unique(evidence, match)
        for match in _collect_environment_signal_matches(text, NON_PRODUCTION_SIGNAL_PATTERNS, field_name):
            _append_unique(non_production_signals, match)
        has_issue_signal = has_issue_signal or _contains_pattern_match(text, ISSUE_SIGNAL_PATTERNS)
        has_training_request_signal = has_training_request_signal or _contains_pattern_match(
            text,
            TRAINING_REQUEST_SIGNAL_PATTERNS,
        )

    for index, comment in enumerate(comments, start=1):
        text = " ".join(filter(None, [str(comment.get("body") or ""), _strip_html(comment.get("html_body"))]))
        source = f"comment #{index}"
        for match in _collect_environment_signal_matches(text, PRODUCTION_SIGNAL_PATTERNS, source):
            _append_unique(evidence, match)
        for match in _collect_environment_signal_matches(text, NON_PRODUCTION_SIGNAL_PATTERNS, source):
            _append_unique(non_production_signals, match)
        has_issue_signal = has_issue_signal or _contains_pattern_match(text, ISSUE_SIGNAL_PATTERNS)
        has_training_request_signal = has_training_request_signal or _contains_pattern_match(
            text,
            TRAINING_REQUEST_SIGNAL_PATTERNS,
        )

    has_strong_production_evidence = any(
        not item.startswith("Mentions production environment.")
        for item in evidence
    )
    is_training_request_only = has_training_request_signal and not has_issue_signal and not has_strong_production_evidence

    return ProductionImpactAssessment(
        is_production_issue=bool(evidence) and (has_issue_signal or has_strong_production_evidence) and not is_training_request_only,
        evidence=evidence,
        non_production_signals=non_production_signals,
    )


def _build_tom_tovar_comment_metadata(comments: list[dict[str, Any]]) -> dict[str, Any]:
    tom_comments: list[dict[str, Any]] = []
    for comment in comments:
        author_id = comment.get("author_id")
        try:
            if author_id is not None and int(author_id) == TOM_TOVAR_USER_ID:
                tom_comments.append(comment)
        except (TypeError, ValueError):
            continue
    tom_comments_sorted = sorted(
        tom_comments,
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    first_comment_at = tom_comments_sorted[0].get("created_at") if tom_comments_sorted else None
    latest_comment_at = None
    latest_comment_summary = None
    if tom_comments:
        latest_comment = tom_comments_sorted[-1]
        latest_comment_at = latest_comment.get("created_at")
        latest_text = str(latest_comment.get("body") or "").strip() or _strip_html(latest_comment.get("html_body"))
        latest_text = re.sub(r"\s+", " ", latest_text).strip()
        if latest_text:
            snippet = latest_text[:160].rstrip()
            if len(latest_text) > 160:
                snippet = f"{snippet}..."
            latest_comment_summary = (
                f"Tom commented {len(tom_comments)} time(s); "
                f"first={first_comment_at or 'Not found'}, latest={latest_comment_at or 'Not found'}; "
                f"latest note: {snippet}"
            )
        else:
            latest_comment_summary = (
                f"Tom commented {len(tom_comments)} time(s); "
                f"first={first_comment_at or 'Not found'}, latest={latest_comment_at or 'Not found'}."
            )
    return {
        "tom_tovar_commented": bool(tom_comments),
        "tom_tovar_comment_marker": TOM_TOVAR_COMMENT_MARKER if tom_comments else None,
        "tom_tovar_comment_count": len(tom_comments),
        "tom_tovar_first_comment_at": first_comment_at,
        "tom_tovar_latest_comment_at": latest_comment_at,
        "tom_tovar_comment_summary": latest_comment_summary,
    }


def _extract_recent_comment_notes(comments: list[dict[str, Any]], requester_id: int | None) -> list[str]:
    comments_sorted = sorted(
        comments,
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    recent_comments = comments_sorted[-3:]
    notes: list[str] = []
    for comment in recent_comments:
        source = _comment_source(comment, requester_id)
        created_at = comment.get("created_at") or "Not found"
        text = _comment_text(comment)

        if _contains_call_mention(text):
            notes.append(f"Recent comment mentions a call/scheduling ({source}, {created_at}).")

        if source == "customer_public_comment":
            snippet = _comment_snippet(comment, limit=100)
            if snippet:
                notes.append(f"Recent customer comment ({created_at}): \"{snippet}\"")

        for datetime_match in DATE_OR_TIME_PATTERN.finditer(text):
            notes.append(
                "Recent comment mentions date/time "
                f'"{datetime_match.group(0)}" ({source}, {created_at}).'
            )
    return notes


def _extract_meeting_scheduled_at(text: str, fallback_year: int | None = None) -> datetime | None:
    match = MEETING_DATETIME_PATTERN.search(text)
    if not match:
        return None

    date_part = match.group("date")
    time_part = match.group("time")
    ampm = match.group("ampm")

    try:
        if "-" in date_part:
            parsed_date = datetime.strptime(date_part, "%Y-%m-%d")
        else:
            month, day, *year_parts = date_part.split("/")
            year = int(year_parts[0]) if year_parts else (fallback_year or datetime.now(timezone.utc).year)
            if year < 100:
                year += 2000
            parsed_date = datetime(year, int(month), int(day))
    except ValueError:
        return None

    hour = 0
    minute = 0
    if time_part:
        try:
            hour, minute = [int(part) for part in time_part.split(":", 1)]
        except ValueError:
            return None
        if ampm:
            ampm_lower = ampm.lower()
            if ampm_lower == "pm" and hour != 12:
                hour += 12
            elif ampm_lower == "am" and hour == 12:
                hour = 0

    return parsed_date.replace(hour=hour, minute=minute, tzinfo=timezone.utc)


def _is_public_agent_comment(comment: dict[str, Any], requester_id: int | None) -> bool:
    return bool(comment.get("public")) and not (requester_id is not None and comment.get("author_id") == requester_id)


def _is_meeting_summary_comment(
    comment: dict[str, Any],
    requester_id: int | None,
    assignee_id: int | None,
) -> bool:
    if not _is_public_agent_comment(comment, requester_id):
        return False
    if assignee_id is not None and comment.get("author_id") != assignee_id:
        return False

    text = _comment_text(comment)
    return any(pattern.search(text) for pattern in MEETING_SUMMARY_PATTERNS)


def _build_meeting_summary_flag(
    comments: list[dict[str, Any]],
    requester_id: int | None,
    assignee_id: int | None,
    updated_at: datetime | None,
) -> TicketTroubleFlag | None:
    public_comments_sorted = sorted(
        [comment for comment in comments if comment.get("public")],
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    if not public_comments_sorted:
        return None

    reference_time = updated_at or datetime.now(timezone.utc)

    for comment in public_comments_sorted:
        text = _comment_text(comment)
        is_meeting_reference, scheduled_at = _classify_meeting_reference(text)
        if not is_meeting_reference:
            continue
        if _is_meeting_summary_comment(comment, requester_id=requester_id, assignee_id=assignee_id):
            continue

        comment_time = _parse_iso_datetime(comment.get("created_at"))
        if scheduled_at is None:
            scheduled_at = _extract_meeting_scheduled_at(text, fallback_year=comment_time.year if comment_time else None)
        if scheduled_at is not None and scheduled_at > reference_time:
            continue

        later_public_comments = [
            candidate
            for candidate in public_comments_sorted
            if (
                (_parse_iso_datetime(candidate.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
                > (comment_time or datetime.min.replace(tzinfo=timezone.utc))
            )
        ]
        if not scheduled_at and not later_public_comments:
            continue

        if any(
            _is_meeting_summary_comment(
                candidate,
                requester_id=requester_id,
                assignee_id=assignee_id,
            )
            for candidate in later_public_comments
        ):
            continue

        meeting_reference = (
            f"scheduled for {scheduled_at.strftime('%Y-%m-%d %H:%M UTC')}"
            if scheduled_at is not None
            else f"mentioned at {comment.get('created_at') or 'an unknown time'}"
        )
        owner_label = "assigned SDE" if assignee_id is not None else "agent"
        return TicketTroubleFlag(
            code="meeting_summary_missing",
            severity="medium",
            message=(
                f"Meeting/call was requested or scheduled ({meeting_reference}), but no later public "
                f"meeting summary notes from the {owner_label} were found."
            ),
        )

    return None


def _build_sev1_customer_data_follow_up_flag(
    ticket: dict[str, Any],
    comments: list[dict[str, Any]],
    requester_id: int | None,
    updated_at: datetime | None,
    status: str | None,
    is_escalated: bool,
) -> TicketTroubleFlag | None:
    if requester_id is None or str(status or "").strip().lower() not in OPEN_TICKET_STATUSES:
        return None
    if not _is_sev1_ticket(ticket, is_escalated=is_escalated):
        return None

    public_comments_sorted = sorted(
        [comment for comment in comments if comment.get("public")],
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    if not public_comments_sorted:
        return None

    outstanding_since: datetime | None = None
    last_touch_at: datetime | None = None

    for comment in public_comments_sorted:
        comment_time = _parse_iso_datetime(comment.get("created_at"))
        if comment_time is None:
            continue

        is_customer_comment = comment.get("author_id") == requester_id
        if is_customer_comment:
            if last_touch_at is None:
                continue
            if _customer_comment_provides_requested_data(comment):
                outstanding_since = None
                last_touch_at = None
                continue
            last_touch_at = comment_time
            continue

        if last_touch_at is not None:
            last_touch_at = comment_time
        if _comment_requests_customer_data(comment):
            outstanding_since = comment_time
            last_touch_at = comment_time

    if outstanding_since is None or last_touch_at is None:
        return None

    reference_time = updated_at or datetime.now(timezone.utc)
    follow_up_deadline = timedelta(hours=SEV1_CUSTOMER_DATA_FOLLOW_UP_SLA_HOURS)
    if reference_time - last_touch_at <= follow_up_deadline:
        return None

    hours_waiting = max(int((reference_time - last_touch_at).total_seconds() // 3600), 1)
    return TicketTroubleFlag(
        code="sev1_customer_data_follow_up_overdue",
        severity="high",
        message=(
            "SEV1 ticket is still waiting on customer-requested data. "
            f"Last public touch while awaiting that data was {hours_waiting}h ago; "
            "follow up hourly until the customer provides it."
        ),
    )


def _extract_merged_ticket_id_from_comment(comment: dict[str, Any]) -> int | None:
    text = " ".join(
        filter(
            None,
            [
                str(comment.get("body") or ""),
                _strip_html(comment.get("html_body")),
            ],
        )
    )
    match = re.search(r"merged\s+into\s+request\s*#\s*(\d+)", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _resolve_merged_ticket_reference(
    ticket_id: int,
    ticket: dict[str, Any],
    comments: list[dict[str, Any]],
) -> tuple[int, dict[str, Any], list[dict[str, Any]], str | None]:
    status = str(ticket.get("status", "")).lower()
    if status not in {"solved", "closed"} or not comments:
        return ticket_id, ticket, comments, None

    last_comment = max(
        comments,
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    referenced_ticket_id = _extract_merged_ticket_id_from_comment(last_comment)
    if referenced_ticket_id is None or referenced_ticket_id == ticket_id:
        return ticket_id, ticket, comments, None

    try:
        referenced_ticket = _prepare_ticket_payload(referenced_ticket_id)
        referenced_comments = zendesk_client.get_ticket_comments(referenced_ticket_id)
        return (
            referenced_ticket_id,
            referenced_ticket,
            referenced_comments,
            (
                f"Ticket {ticket_id} appears merged into request #{referenced_ticket_id}; "
                "using referenced ticket evidence."
            ),
        )
    except Exception as exc:
        logger.warning(
            "Failed to resolve merged ticket reference from %s to %s: %s",
            ticket_id,
            referenced_ticket_id,
            exc,
        )
        return ticket_id, ticket, comments, None


def _is_stacktrace_attachment_filename(file_name: str | None) -> bool:
    if not file_name:
        return False
    lowered = file_name.lower()
    evidence_extensions = (".ips", ".crash", ".dmp")
    evidence_keywords = (
        "stacktrace",
        "stack_trace",
        "stack-trace",
        "backtrace",
        "deobfuscat",
        "deobfuscated",
        "crashlytics",
    )
    if lowered.endswith(evidence_extensions):
        return True
    if any(keyword in lowered for keyword in evidence_keywords):
        return True
    return lowered.endswith(".log") and any(keyword in lowered for keyword in ("crash", "stack", "fatal", "exception"))


def _classify_crash_attachment(file_name: str | None, content_type: str | None) -> str | None:
    lowered_name = str(file_name or "").lower()
    lowered_content_type = str(content_type or "").lower()

    is_video = lowered_name.endswith(VIDEO_ATTACHMENT_EXTENSIONS) or lowered_content_type.startswith("video/")
    is_image = lowered_name.endswith(IMAGE_ATTACHMENT_EXTENSIONS) or lowered_content_type.startswith("image/")
    has_crash_keyword = any(keyword in lowered_name for keyword in CRASH_ATTACHMENT_KEYWORDS)

    if _is_stacktrace_attachment_filename(lowered_name):
        return "stacktrace"
    if is_video and has_crash_keyword:
        return "replication_video"
    if lowered_name.endswith(".log") and has_crash_keyword:
        return "crash_log"
    if is_image and has_crash_keyword:
        return "crash_screenshot"
    if has_crash_keyword:
        return "crash_artifact"
    return None


def _comment_source(comment: dict[str, Any], requester_id: int | None) -> str:
    if not bool(comment.get("public")):
        return "internal_note"
    if requester_id is not None and comment.get("author_id") == requester_id:
        return "customer_public_comment"
    return "agent_public_comment"


def _build_crash_attachment_summary(
    comments: list[dict[str, Any]],
    requester_id: int | None,
    enabled: bool = True,
) -> CrashAttachmentSummary:
    if not enabled:
        return CrashAttachmentSummary()

    signals: list[CrashAttachmentSignal] = []
    stacktrace_files: list[str] = []
    replication_videos: list[str] = []
    crash_related_files: list[str] = []

    for comment in comments:
        for attachment in (comment.get("attachments") or []):
            file_name = str(attachment.get("file_name") or "")
            content_type = str(attachment.get("content_type") or "")
            evidence_type = _classify_crash_attachment(file_name=file_name, content_type=content_type)
            if evidence_type is None:
                continue

            signal = CrashAttachmentSignal(
                file_name=file_name,
                evidence_type=evidence_type,
                source=_comment_source(comment, requester_id),
                content_type=content_type or None,
                size=attachment.get("size"),
            )
            signals.append(signal)
            crash_related_files.append(file_name)
            if evidence_type == "stacktrace":
                stacktrace_files.append(file_name)
            if evidence_type == "replication_video":
                replication_videos.append(file_name)

    return CrashAttachmentSummary(
        has_crash_related_attachments=bool(signals),
        has_stacktrace_attachment=bool(stacktrace_files),
        has_replication_video=bool(replication_videos),
        stacktrace_files=stacktrace_files,
        replication_videos=replication_videos,
        crash_related_files=crash_related_files,
        signals=signals,
    )


def _is_title_structured(subject: str | None) -> bool:
    if not subject:
        return False
    segments = [segment.strip() for segment in subject.split("|")]
    segments = [segment for segment in segments if segment]
    return len(segments) >= 3


def _is_feature_request_ticket(subject: str | None) -> bool:
    if not subject:
        return False
    return re.search(r"\bfeature request\b", subject, re.IGNORECASE) is not None


def _adjust_trouble_risk_score(status: str | None, base_risk_score: int) -> int:
    if str(status or "").strip().lower() == "pending":
        return max(0, base_risk_score - PENDING_TICKET_PRIORITY_DISCOUNT)
    return base_risk_score


def _has_internal_tag_title_mismatch(ticket: dict[str, Any]) -> bool:
    tags = {str(tag).strip().lower() for tag in (ticket.get("tags") or [])}
    if "internal" not in tags:
        return False

    subject = str(ticket.get("subject") or "")
    return re.search(r"\binternal\b", subject, re.IGNORECASE) is None


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


def _has_internal_first_comment(comments: list[dict[str, Any]]) -> bool:
    if not comments:
        return False

    first_comment = min(
        comments,
        key=lambda comment: _parse_iso_datetime(comment.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    return not bool(first_comment.get("public"))


def _is_escalated_ticket(ticket: dict[str, Any]) -> bool:
    escalation_display = ticket.get("escalation_status_display")
    escalation_tag = ticket.get("escalation_status_tag")
    if escalation_display or escalation_tag:
        return True

    custom_fields = ticket.get("custom_fields") if isinstance(ticket.get("custom_fields"), dict) else {}
    escalation_value = custom_fields.get("Escalation Status") or custom_fields.get("Escalation")
    if escalation_value is None:
        return False

    normalized_value = str(escalation_value).strip().lower()
    return normalized_value not in {"", "n/a", "none", "null", "not set"}


def _build_ticket_trouble_assessment(
    ticket: dict[str, Any],
    comments: list[dict[str, Any]],
    initial_response_sla_minutes: int,
    high_priority_stale_hours: int,
) -> TicketTroubleAssessment:
    flags: list[TicketTroubleFlag] = []

    ticket_id = int(ticket.get("id"))
    subject = ticket.get("subject")
    description = ticket.get("description")
    status = ticket.get("status")
    priority = ticket.get("priority")
    requester_id = ticket.get("requester_id")
    assignee_id = ticket.get("assignee_id")
    tags = set(ticket.get("tags") or [])
    created_at = _parse_iso_datetime(ticket.get("created_at"))
    updated_at = _parse_iso_datetime(ticket.get("updated_at"))
    custom_fields = ticket.get("custom_fields") if isinstance(ticket.get("custom_fields"), dict) else {}
    is_escalated = _is_escalated_ticket(ticket)
    status_with = str(custom_fields.get("Status With") or "").strip().lower()
    is_feature_request = _is_feature_request_ticket(subject)
    if is_feature_request:
        production_impact = ProductionImpactAssessment()
    else:
        production_impact = _build_production_impact_assessment(ticket=ticket, comments=comments)

    if is_feature_request:
        tom_tovar_comment_metadata = _build_tom_tovar_comment_metadata(comments)
        recent_comment_notes = _extract_recent_comment_notes(comments=comments, requester_id=requester_id)
        return TicketTroubleAssessment(
            ticket_id=ticket_id,
            ticket_url=_ticket_url(ticket_id) or "",
            ticket_link=_ticket_link(ticket_id) or "",
            subject=subject,
            status=status,
            priority="low",
            is_escalated=is_escalated,
            priority_interpretation="Feature request title detected: treat as low priority with no operational risk.",
            in_trouble=False,
            risk_score=0,
            flags=[],
            crash_attachment_summary=None,
            production_impact=production_impact,
            recent_comment_notes=recent_comment_notes,
            tom="☑" if tom_tovar_comment_metadata["tom_tovar_commented"] else "☐",
            tom_tovar_commented=tom_tovar_comment_metadata["tom_tovar_commented"],
            tom_tovar_comment_marker=tom_tovar_comment_metadata["tom_tovar_comment_marker"],
            tom_tovar_comment_count=tom_tovar_comment_metadata["tom_tovar_comment_count"],
            tom_tovar_latest_comment_at=tom_tovar_comment_metadata["tom_tovar_latest_comment_at"],
            tom_tovar_comment_summary=tom_tovar_comment_metadata["tom_tovar_comment_summary"],
        )

    public_comments = [c for c in comments if c.get("public")]
    crash_tag_reviewed = "crash_reviewed" in tags
    is_unreviewed_crash_ticket = "crash_detected" in tags and not crash_tag_reviewed
    crash_attachment_summary = _build_crash_attachment_summary(
        comments=comments,
        requester_id=requester_id,
        enabled=is_unreviewed_crash_ticket,
    )
    public_comments_sorted = sorted(
        public_comments,
        key=lambda c: _parse_iso_datetime(c.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    has_internal_first_comment = _has_internal_first_comment(comments)

    if not _is_title_structured(subject):
        flags.append(
            TicketTroubleFlag(
                code="title_incorrect",
                severity="medium",
                message="Ticket title is missing expected structured segments (Customer | Context | Issue).",
            )
        )

    if _has_internal_tag_title_mismatch(ticket):
        flags.append(
            TicketTroubleFlag(
                code="internal_tag_title_mismatch",
                severity="medium",
                message=(
                    'Ticket has the "internal" tag, but the title does not include the word "internal"; '
                    "possible system tagging/title-sync issue."
                ),
            )
        )

    if production_impact.is_production_issue:
        evidence_preview = "; ".join(production_impact.evidence[:3])
        flags.append(
            TicketTroubleFlag(
                code="production_user_impact",
                severity="high",
                message=(
                    "Ticket indicates a live production issue affecting real users/customers; "
                    f"prioritize above UAT/DEV/testing issues. Evidence: {evidence_preview}"
                ),
            )
        )

    customer_urgency_flag = _build_customer_urgency_flag(
        ticket=ticket,
        comments=comments,
        requester_id=requester_id,
    )
    if customer_urgency_flag is not None:
        flags.append(customer_urgency_flag)

    unhappy_comment_flag = _build_customer_unhappy_flag(comments=comments, requester_id=requester_id)
    if unhappy_comment_flag is not None:
        flags.append(unhappy_comment_flag)

    customer_repeated_pressure_flag = _build_customer_repeated_pressure_flag(
        comments=comments,
        requester_id=requester_id,
    )
    if customer_repeated_pressure_flag is not None:
        flags.append(customer_repeated_pressure_flag)

    ticket_report_request_flag = _build_ticket_report_request_flag(
        ticket=ticket,
        comments=comments,
        requester_id=requester_id,
    )
    if ticket_report_request_flag is not None:
        flags.append(ticket_report_request_flag)

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

    if created_at is not None and first_public_agent_response_at is None and not has_internal_first_comment:
        reference_time = updated_at or datetime.now(timezone.utc)
        response_delay_minutes = int(max((reference_time - created_at).total_seconds(), 0) // 60)
        if response_delay_minutes > initial_response_sla_minutes:
            flags.append(
                TicketTroubleFlag(
                    code="missing_initial_response",
                    severity="high",
                    message=(
                        "No public agent response found after "
                        f"{response_delay_minutes}m (SLA {initial_response_sla_minutes}m)."
                    ),
                )
            )
    elif created_at is not None and first_public_agent_response_at is not None and not has_internal_first_comment:
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
    customer_response_flag = _build_customer_comment_response_flag(
        comments=comments,
        requester_id=requester_id,
        updated_at=updated_at,
        status=status,
        production_impact=production_impact,
    )
    if customer_response_flag is not None:
        flags.append(customer_response_flag)

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

    stale_hours = ticket.get("stale_age_hours")
    if stale_hours is None and updated_at is not None:
        stale_hours = int(max((datetime.now(timezone.utc) - updated_at).total_seconds(), 0) // 3600)

    if stale_hours is not None and int(stale_hours) > high_priority_stale_hours and status in OPEN_TICKET_STATUSES:
        if is_escalated and priority in {"high", "urgent"}:
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
        elif "support" in status_with:
            flags.append(
                TicketTroubleFlag(
                    code="support_owned_no_recent_updates",
                    severity="high",
                    message=(
                        f"Non-escalated support-owned ticket has no recent update for {int(stale_hours)}h "
                        f"(threshold {high_priority_stale_hours}h)."
                    ),
                )
            )

    if is_unreviewed_crash_ticket:
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
                file_name = str(attachment.get("file_name") or "")
                attachment_type = _classify_crash_attachment(
                    file_name=file_name,
                    content_type=attachment.get("content_type"),
                )
                if attachment_type in {"stacktrace", "crash_log", "crash_artifact"}:
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

    meeting_summary_flag = _build_meeting_summary_flag(
        comments=comments,
        requester_id=requester_id,
        assignee_id=assignee_id,
        updated_at=updated_at,
    )
    if meeting_summary_flag is not None:
        flags.append(meeting_summary_flag)

    sev1_customer_data_follow_up_flag = _build_sev1_customer_data_follow_up_flag(
        ticket=ticket,
        comments=comments,
        requester_id=requester_id,
        updated_at=updated_at,
        status=status,
        is_escalated=is_escalated,
    )
    if sev1_customer_data_follow_up_flag is not None:
        flags.append(sev1_customer_data_follow_up_flag)

    recent_comment_notes = _extract_recent_comment_notes(comments=comments, requester_id=requester_id)

    sorted_flags = sorted(
        flags,
        key=lambda flag: (
            -TROUBLE_FLAG_WEIGHTS.get(flag.code, SEVERITY_FALLBACK_WEIGHTS.get(flag.severity, 5)),
            -SEVERITY_RANK.get(flag.severity, 0),
            flag.code,
        ),
    )
    base_risk_score = min(
        100,
        sum(
            TROUBLE_FLAG_WEIGHTS.get(flag.code, SEVERITY_FALLBACK_WEIGHTS.get(flag.severity, 5))
            for flag in sorted_flags
        ),
    )
    risk_score = _adjust_trouble_risk_score(status=status, base_risk_score=base_risk_score)
    tom_tovar_comment_metadata = _build_tom_tovar_comment_metadata(comments)

    return TicketTroubleAssessment(
        ticket_id=ticket_id,
        ticket_url=_ticket_url(ticket_id) or "",
        ticket_link=_ticket_link(ticket_id) or "",
        subject=subject,
        status=status,
        priority=priority,
        is_escalated=is_escalated,
        priority_interpretation=(
            "Escalated ticket: Zendesk priority mirrors ENG priority."
            if is_escalated
            else (
                "Pending ticket: lower priority by default because a solution was communicated and customer "
                "confirmation is still pending before solve."
                if str(status or "").strip().lower() == "pending"
                else "Non-escalated ticket: Zendesk priority is not treated as severity; use flags and risk score."
            )
        ),
        in_trouble=bool(sorted_flags),
        risk_score=risk_score,
        flags=sorted_flags,
        crash_attachment_summary=crash_attachment_summary,
        production_impact=production_impact,
        recent_comment_notes=recent_comment_notes,
        tom="☑" if tom_tovar_comment_metadata["tom_tovar_commented"] else "☐",
        tom_tovar_commented=tom_tovar_comment_metadata["tom_tovar_commented"],
        tom_tovar_comment_marker=tom_tovar_comment_metadata["tom_tovar_comment_marker"],
        tom_tovar_comment_count=tom_tovar_comment_metadata["tom_tovar_comment_count"],
        tom_tovar_latest_comment_at=tom_tovar_comment_metadata["tom_tovar_latest_comment_at"],
        tom_tovar_comment_summary=tom_tovar_comment_metadata["tom_tovar_comment_summary"],
    )


def _build_ticket_trouble_markdown_list(tickets: list[TicketTroubleAssessment]) -> str:
    if not tickets:
        return "No tickets matched the trouble scan."

    lines: list[str] = []
    for ticket in tickets:
        ticket_ref = ticket.ticket_link or f"#{ticket.ticket_id}"
        subject = ticket.subject or "Untitled"
        status = ticket.status or "unknown"
        highlights: list[str] = []
        if any(flag.code == "customer_urgency" for flag in ticket.flags):
            highlights.append("CUSTOMER-URGENT")
        if any(flag.code == "production_user_impact" for flag in ticket.flags):
            highlights.append("PROD-IMPACT")
        if any(flag.code == "production_customer_comment_no_response" for flag in ticket.flags):
            highlights.append("PROD-NO-RESPONSE")
        if any(flag.code == "customer_unhappy" for flag in ticket.flags):
            highlights.append("CUSTOMER-UNHAPPY")
        if any(flag.code == "customer_repeated_pressure" for flag in ticket.flags):
            highlights.append("CUSTOMER-PRESSURE")
        highlight_text = f" | highlights={','.join(highlights)}" if highlights else ""
        lines.append(f"- {ticket_ref} | {subject} | status={status} | risk={ticket.risk_score}{highlight_text}")
    return "\n".join(lines)


def _sort_ticket_assessments_by_importance(
    assessments: list[TicketTroubleAssessment],
) -> list[TicketTroubleAssessment]:
    return sorted(
        assessments,
        key=lambda ticket: (ticket.in_trouble, ticket.risk_score, ticket.ticket_id),
        reverse=True,
    )


@mcp.prompt(name="analyze-ticket", description="Analyze a Zendesk ticket and provide insights")
def analyze_ticket_prompt(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to analyze")],
) -> str:
    return TICKET_ANALYSIS_TEMPLATE.format(
        ticket_id=ticket_id,
        ticket_link=_ticket_link(ticket_id),
        attribution_guardrails=ATTRIBUTION_GUARDRAILS,
    ).strip()


@mcp.prompt(
    name="draft-ticket-response",
    description="Draft a professional response to a Zendesk ticket",
)

@mcp.prompt(
    name="ticket-title-review-policy",
    description="Define the policy for reviewing Zendesk ticket title structure",
)
def ticket_title_review_policy_prompt() -> str:
    return TITLE_REVIEW_POLICY_TEMPLATE.format(attribution_guardrails=ATTRIBUTION_GUARDRAILS).strip()

@mcp.prompt(
    name="review-ticket-title",
    description="Review a specific Zendesk ticket title using the title review policy",
)
def review_ticket_title_prompt(
    ticket_id: Annotated[int, Field(description="The Zendesk ticket ID to review")],
) -> str:
    return (
        TITLE_REVIEW_POLICY_TEMPLATE.format(attribution_guardrails=ATTRIBUTION_GUARDRAILS).strip()
        + "\n\n"
        + REVIEW_SINGLE_TICKET_TEMPLATE.format(ticket_id=ticket_id, ticket_link=_ticket_link(ticket_id)).strip()
    )


def draft_ticket_response_prompt(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to respond to")],
) -> str:
    return COMMENT_DRAFT_TEMPLATE.format(
        ticket_id=ticket_id,
        ticket_link=_ticket_link(ticket_id),
        attribution_guardrails=ATTRIBUTION_GUARDRAILS,
    ).strip()


@mcp.tool(name="get_ticket", description="Retrieve a Zendesk ticket by its ID")
def get_ticket(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to retrieve")],
) -> str:
    ticket = _prepare_ticket_payload(ticket_id)
    return json.dumps(ticket)


@mcp.tool(
    name="get_user",
    description="Retrieve a Zendesk user by ID (includes name/email for ID translation)",
)
def get_user(
    user_id: Annotated[int, Field(description="Zendesk user ID to retrieve")],
) -> str:
    user = zendesk_client.get_user(user_id)
    return json.dumps(UserItem.model_validate(user).model_dump(mode="json"))


@mcp.tool(
    name="search_users",
    description="Search Zendesk users by name/email for reverse lookup and filtering",
    structured_output=True,
)
def search_users(
    query: Annotated[str, Field(description="Name/email/id text to search for")],
    page: Annotated[int, Field(description="Page number")] = 1,
    per_page: Annotated[int, Field(description="Results per page (max 100)")] = 25,
) -> SearchUsersResult:
    result = zendesk_client.search_users(query=query, page=page, per_page=per_page)
    payload = {
        "users": result.get("users", []),
        "count": result.get("count", 0),
        "query": result.get("query", query),
        "page": result.get("page", page),
        "per_page": result.get("per_page", per_page),
        "has_more": bool(result.get("next_page")),
        "next_page": page + 1 if result.get("next_page") else None,
        "previous_page": page - 1 if page > 1 else None,
    }
    return SearchUsersResult.model_validate(payload)


@mcp.tool(
    name="translate_user_ids",
    description="Translate Zendesk user IDs to user profiles (name/email) in bulk",
    structured_output=True,
)
def translate_user_ids(
    user_ids: Annotated[list[int], Field(description="User IDs to translate")],
) -> TranslateUsersResult:
    users_by_id = zendesk_client.get_users_by_ids(user_ids)
    payload = {
        "users_by_id": {str(user_id): user for user_id, user in users_by_id.items()},
        "missing_ids": [int(user_id) for user_id in user_ids if int(user_id) not in users_by_id],
    }
    return TranslateUsersResult.model_validate(payload)


@mcp.tool(
    name="resolve_user_identifier",
    description="Resolve an identifier (id, email, or name) into a single user profile",
)
def resolve_user_identifier(
    identifier: Annotated[str, Field(description="User identifier: id/email/name")],
) -> str:
    user = zendesk_client.resolve_user(identifier)
    if user is None:
        return json.dumps({"identifier": identifier, "resolved": False, "user": None})
    return json.dumps(
        {
            "identifier": identifier,
            "resolved": True,
            "user": UserItem.model_validate(user).model_dump(mode="json"),
        }
    )


@mcp.tool(
    name="get_ticket_summary",
    description="Retrieve a Zendesk ticket as a compact display-ready summary",
)
def get_ticket_summary(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to summarize")],
) -> str:
    ticket = _prepare_ticket_payload(ticket_id)
    comments = zendesk_client.get_ticket_comments(ticket_id)
    resolved_ticket_id, ticket, comments, merge_note = _resolve_merged_ticket_reference(
        ticket_id=ticket_id,
        ticket=ticket,
        comments=comments,
    )
    assessment = _build_ticket_trouble_assessment(
        ticket=ticket,
        comments=comments,
        initial_response_sla_minutes=DEFAULT_INITIAL_RESPONSE_SLA_MINUTES,
        high_priority_stale_hours=DEFAULT_HIGH_PRIORITY_STALE_HOURS,
    )
    summary = _build_ticket_summary(ticket)
    alert_lines = [
        "",
        "## Trouble Scan",
        f"Reviewed Ticket ID: {resolved_ticket_id}",
        f"Escalated: {'Yes' if assessment.is_escalated else 'No'}",
        f"Production Issue: {'Yes' if assessment.production_impact.is_production_issue else 'No'}",
        f"Priority Interpretation: {assessment.priority_interpretation}",
        f"In Trouble: {'Yes' if assessment.in_trouble else 'No'}",
        f"Risk Score: {assessment.risk_score}",
    ]
    if merge_note:
        alert_lines.append(f"Note: {merge_note}")
    if assessment.tom_tovar_commented:
        alert_lines.append(
            f"{assessment.tom_tovar_comment_marker} "
            f"(count={assessment.tom_tovar_comment_count}, latest={assessment.tom_tovar_latest_comment_at or 'Not found'})"
        )
        if assessment.tom_tovar_comment_summary:
            alert_lines.append(f"Tom Summary: {assessment.tom_tovar_comment_summary}")
    crash_attachments = assessment.crash_attachment_summary
    if crash_attachments is not None:
        alert_lines.append(
            f"Crash-related attachments available: {'Yes' if crash_attachments.has_crash_related_attachments else 'No'}"
        )
        stacktrace_files = ", ".join(crash_attachments.stacktrace_files) if crash_attachments.stacktrace_files else "Not found"
        replication_videos = (
            ", ".join(crash_attachments.replication_videos) if crash_attachments.replication_videos else "Not found"
        )
        other_files = [
            file_name
            for file_name in crash_attachments.crash_related_files
            if file_name not in set(crash_attachments.stacktrace_files + crash_attachments.replication_videos)
        ]
        other_related = ", ".join(other_files) if other_files else "Not found"
        alert_lines.append(f"Stacktrace attachments: {stacktrace_files}")
        alert_lines.append(f"Replication path video: {replication_videos}")
        alert_lines.append(f"Other crash-related attachments: {other_related}")
    if assessment.production_impact.is_production_issue:
        alert_lines.append(f"Production Evidence: {'; '.join(assessment.production_impact.evidence)}")
    elif assessment.production_impact.non_production_signals:
        alert_lines.append(
            "Environment Signals: "
            f"non-production only ({'; '.join(assessment.production_impact.non_production_signals)})"
        )
    if assessment.flags:
        alert_lines.append("Flags:")
        for flag in assessment.flags:
            alert_lines.append(f"- [{flag.severity.upper()}] {flag.code}: {flag.message}")
    else:
        alert_lines.append("Flags: none")
    return "\n".join([summary, *alert_lines])


@mcp.tool(
    name="review_ticket",
    description="Fetch ticket evidence and the review rubric for a Zendesk ticket",
)
def review_ticket(
    ticket_id: Annotated[int, Field(description="The ID of the ticket to review")],
) -> str:
    ticket = _prepare_ticket_payload(ticket_id)
    comments = zendesk_client.get_ticket_comments(ticket_id)
    resolved_ticket_id, ticket, comments, merge_note = _resolve_merged_ticket_reference(
        ticket_id=ticket_id,
        ticket=ticket,
        comments=comments,
    )
    if merge_note:
        ticket["merge_reference_note"] = merge_note
    if resolved_ticket_id != ticket_id:
        ticket["merged_from_ticket_id"] = ticket_id
    ticket.update(_build_tom_tovar_comment_metadata(comments))
    ticket["production_impact"] = _build_production_impact_assessment(ticket=ticket, comments=comments).model_dump()
    ticket_tags = set(ticket.get("tags") or [])
    return build_ticket_analysis_input(
        ticket_id=resolved_ticket_id,
        ticket=ticket,
        comments=comments,
        attachment_evidence_summary=_build_crash_attachment_summary(
            comments=comments,
            requester_id=ticket.get("requester_id"),
            enabled="crash_detected" in ticket_tags and "crash_reviewed" not in ticket_tags,
        ).model_dump(),
        rubric=TICKET_ANALYSIS_TEMPLATE.format(
            ticket_id=resolved_ticket_id,
            ticket_link=_ticket_link(resolved_ticket_id),
            attribution_guardrails=ATTRIBUTION_GUARDRAILS,
        ),
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
    ] = DEFAULT_INITIAL_RESPONSE_SLA_MINUTES,
    high_priority_stale_hours: Annotated[
        int,
        Field(description="Threshold in hours for stale escalated high-priority or stale support-owned tickets."),
    ] = DEFAULT_HIGH_PRIORITY_STALE_HOURS,
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
        if str(ticket.get("status", "")).lower() in {"solved", "closed"}:
            continue
        if _is_feature_request_ticket(ticket.get("subject")):
            continue
        ticket_id = ticket.get("id")
        if ticket_id is None:
            continue
        full_ticket = _prepare_ticket_payload(int(ticket_id))
        if str(full_ticket.get("status", "")).lower() in {"solved", "closed"}:
            continue
        if _is_feature_request_ticket(full_ticket.get("subject")):
            continue
        comments = zendesk_client.get_ticket_comments(int(ticket_id))
        assessment = _build_ticket_trouble_assessment(
            ticket=full_ticket,
            comments=comments,
            initial_response_sla_minutes=initial_response_sla_minutes,
            high_priority_stale_hours=high_priority_stale_hours,
        )
        status_lower = str(full_ticket.get("status", "")).lower()
        if status_lower == "new":
            has_overdue_missing_initial_response = any(
                flag.code == "missing_initial_response" for flag in assessment.flags
            )
            if not has_overdue_missing_initial_response:
                continue
        assessments.append(assessment)

    assessments = _sort_ticket_assessments_by_importance(assessments)
    in_trouble_count = len([ticket for ticket in assessments if ticket.in_trouble])
    return ScanTicketsInTroubleResult(
        created_last_hours=created_last_hours,
        scanned_count=len(assessments),
        in_trouble_count=in_trouble_count,
        ticket_list_markdown=_build_ticket_trouble_markdown_list(assessments),
        tickets=assessments,
    )


@mcp.tool(
    name="scan_crash_tickets_in_trouble",
    description="Scan open non-internal tickets with a crash-related tag and flag tickets likely in trouble based on QA process checks",
    structured_output=True,
)
def scan_crash_tickets_in_trouble(
    tag: Annotated[
        str,
        Field(description="Crash-related tag to scan, e.g. crash_detected."),
    ] = "crash_detected",
    max_results: Annotated[
        int,
        Field(description="Maximum number of tagged tickets to inspect (max 1000)."),
    ] = 250,
    per_page: Annotated[
        int,
        Field(description="How many matching tickets to fetch per page from Zendesk search (max 100)."),
    ] = 100,
    exclude_internal: Annotated[
        bool,
        Field(description="Exclude tickets tagged internal from scan results."),
    ] = True,
    initial_response_sla_minutes: Annotated[
        int,
        Field(description="SLA threshold for first public agent response in minutes."),
    ] = DEFAULT_INITIAL_RESPONSE_SLA_MINUTES,
    high_priority_stale_hours: Annotated[
        int,
        Field(description="Threshold in hours for stale escalated high-priority or stale support-owned tickets."),
    ] = DEFAULT_HIGH_PRIORITY_STALE_HOURS,
) -> ScanCrashTicketsInTroubleResult:
    search_result = zendesk_client.search_open_tickets_by_tag(
        tag=tag,
        max_results=max_results,
        per_page=per_page,
        include_solved=False,
        exclude_internal=exclude_internal,
    )

    assessments: list[TicketTroubleAssessment] = []
    for ticket in search_result.get("tickets", []):
        if str(ticket.get("status", "")).lower() != "open":
            continue
        ticket_id = ticket.get("id")
        if ticket_id is None:
            continue
        full_ticket = _prepare_ticket_payload(int(ticket_id))
        if str(full_ticket.get("status", "")).lower() != "open":
            continue
        comments = zendesk_client.get_ticket_comments(int(ticket_id))
        assessment = _build_ticket_trouble_assessment(
            ticket=full_ticket,
            comments=comments,
            initial_response_sla_minutes=initial_response_sla_minutes,
            high_priority_stale_hours=high_priority_stale_hours,
        )
        assessments.append(assessment)

    assessments = _sort_ticket_assessments_by_importance(assessments)
    in_trouble_count = len([ticket for ticket in assessments if ticket.in_trouble])
    return ScanCrashTicketsInTroubleResult(
        tag=tag,
        scanned_count=len(assessments),
        in_trouble_count=in_trouble_count,
        total_matches=int(search_result.get("total_matches") or len(assessments)),
        retrieved_count=int(search_result.get("retrieved_count") or len(search_result.get("tickets", []))),
        truncated=bool(search_result.get("truncated")),
        ticket_list_markdown=_build_ticket_trouble_markdown_list(assessments),
        tickets=assessments,
    )


@mcp.tool(
    name="get_important_tickets_today",
    description=(
        "Find tickets that matter today based on recent activity or stale follow-up risk, "
        "then rank them with the ticket trouble assessment"
    ),
    structured_output=True,
)
def get_important_tickets_today(
    recent_activity_hours: Annotated[
        int,
        Field(description="Include tickets updated in the last N hours, regardless of when they were created."),
    ] = 24,
    stale_hours: Annotated[
        int,
        Field(description="Also include tickets that have not been updated in the last N hours."),
    ] = DEFAULT_HIGH_PRIORITY_STALE_HOURS,
    per_page: Annotated[
        int,
        Field(description="Maximum tickets to fetch from each candidate query (max 100)."),
    ] = 50,
    agent: Annotated[
        str | None,
        Field(description="Optional assignee filter. Can be agent id, email, or name."),
    ] = None,
    organization: Annotated[
        str | None,
        Field(description="Optional organization name filter."),
    ] = None,
    exclude_internal: Annotated[
        bool,
        Field(description="Exclude tickets tagged internal from scan results."),
    ] = True,
    initial_response_sla_minutes: Annotated[
        int,
        Field(description="SLA threshold for first public agent response in minutes."),
    ] = DEFAULT_INITIAL_RESPONSE_SLA_MINUTES,
    high_priority_stale_hours: Annotated[
        int,
        Field(description="Threshold in hours for stale escalated high-priority or stale support-owned tickets."),
    ] = DEFAULT_HIGH_PRIORITY_STALE_HOURS,
) -> GetImportantTicketsTodayResult:
    bounded_per_page = min(per_page, 100)
    recent_result = zendesk_client.get_tickets(
        page=1,
        per_page=bounded_per_page,
        sort_by="updated_at",
        sort_order="desc",
        agent=agent,
        organization=organization,
        last_hours=recent_activity_hours,
        exclude_internal=exclude_internal,
    )
    stale_result = zendesk_client.get_tickets(
        page=1,
        per_page=bounded_per_page,
        sort_by="updated_at",
        sort_order="asc",
        agent=agent,
        organization=organization,
        stale_hours=stale_hours,
        exclude_internal=exclude_internal,
    )

    candidate_ticket_ids: list[int] = []
    seen_ticket_ids: set[int] = set()
    for result in (recent_result, stale_result):
        for ticket in result.get("tickets", []):
            ticket_id = ticket.get("id")
            if ticket_id is None:
                continue
            normalized_ticket_id = int(ticket_id)
            if normalized_ticket_id in seen_ticket_ids:
                continue
            seen_ticket_ids.add(normalized_ticket_id)
            candidate_ticket_ids.append(normalized_ticket_id)

    assessments: list[TicketTroubleAssessment] = []
    for ticket_id in candidate_ticket_ids:
        full_ticket = _prepare_ticket_payload(ticket_id)
        if str(full_ticket.get("status", "")).lower() in {"solved", "closed"}:
            continue
        if _is_feature_request_ticket(full_ticket.get("subject")):
            continue
        comments = zendesk_client.get_ticket_comments(ticket_id)
        assessments.append(
            _build_ticket_trouble_assessment(
                ticket=full_ticket,
                comments=comments,
                initial_response_sla_minutes=initial_response_sla_minutes,
                high_priority_stale_hours=high_priority_stale_hours,
            )
        )

    assessments = _sort_ticket_assessments_by_importance(assessments)
    in_trouble_count = len([ticket for ticket in assessments if ticket.in_trouble])
    return GetImportantTicketsTodayResult(
        filters=ImportantTodayFilters(
            agent=agent,
            organization=organization,
            recent_activity_hours=recent_activity_hours,
            stale_hours=stale_hours,
            exclude_internal=exclude_internal,
        ),
        candidate_count=len(assessments),
        in_trouble_count=in_trouble_count,
        ticket_list_markdown=_build_ticket_trouble_markdown_list(assessments),
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
    description="Return a random lightweight sample of resolved tickets (solved/closed) for an agent within a date range",
    structured_output=True,
)
def sample_solved_tickets_for_agent(
    agent: Annotated[str, Field(description="Agent assignee filter. Can be agent id, email, or name.")],
    solved_after: Annotated[
        str,
        Field(description="Inclusive lower bound date for resolved tickets (based on updated_at), e.g. 2026-02-01."),
    ],
    solved_before: Annotated[
        str,
        Field(description="Exclusive upper bound date for resolved tickets (based on updated_at), e.g. 2026-03-01."),
    ],
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
        resolve_agent_id=True,
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
    description="Sample resolved tickets (solved/closed) for an agent in a date range and return a ticket QA review packet",
    structured_output=True,
)
def review_random_solved_tickets_for_agent(
    agent: Annotated[str, Field(description="Agent assignee filter. Can be agent id, email, or name.")],
    solved_after: Annotated[
        str,
        Field(description="Inclusive lower bound date for resolved tickets (based on updated_at), e.g. 2026-02-01."),
    ],
    solved_before: Annotated[
        str,
        Field(description="Exclusive upper bound date for resolved tickets (based on updated_at), e.g. 2026-03-01."),
    ],
    count: Annotated[int, Field(description="How many random tickets to review for ticket QA.")] = 4,
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
        ticket = _prepare_ticket_payload(ticket_id)
        comments = zendesk_client.get_ticket_comments(ticket_id)
        resolved_ticket_id, ticket, comments, merge_note = _resolve_merged_ticket_reference(
            ticket_id=ticket_id,
            ticket=ticket,
            comments=comments,
        )
        if merge_note:
            ticket["merge_reference_note"] = merge_note
        if resolved_ticket_id != ticket_id:
            ticket["merged_from_ticket_id"] = ticket_id
        ticket.update(_build_tom_tovar_comment_metadata(comments))
        production_impact = _build_production_impact_assessment(ticket=ticket, comments=comments)
        ticket["production_impact"] = production_impact.model_dump()
        reviews.append(
            {
                "ticket_id": resolved_ticket_id,
                "ticket": ticket,
                "comments": comments,
                "production_impact": production_impact.model_dump(),
                "attachment_evidence_summary": _build_crash_attachment_summary(
                    comments=comments,
                    requester_id=ticket.get("requester_id"),
                    enabled="crash_detected" in set(ticket.get("tags") or [])
                    and "crash_reviewed" not in set(ticket.get("tags") or []),
                ).model_dump(),
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
            "production_ticket_ids": [
                review["ticket_id"]
                for review in reviews
                if review.get("production_impact", {}).get("is_production_issue")
            ],
            "production_ticket_links": [
                (_ticket_link(review["ticket_id"]) or "")
                for review in reviews
                if review.get("production_impact", {}).get("is_production_issue")
            ],
            "production_ticket_count": len(
                [review for review in reviews if review.get("production_impact", {}).get("is_production_issue")]
            ),
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
    comments = _hydrate_comment_author_fields(comments)
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
