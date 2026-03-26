# Zendesk MCP Server

![ci](https://github.com/reminia/zendesk-mcp-server/actions/workflows/ci.yml/badge.svg)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A Model Context Protocol server for Zendesk.

This server provides a comprehensive integration with Zendesk. It offers:

- Tools for retrieving and managing Zendesk tickets and comments
- Specialized prompts for ticket analysis and response drafting
- Full access to the Zendesk Help Center articles as knowledge base

![demo](https://res.cloudinary.com/leecy-me/image/upload/v1736410626/open/zendesk_yunczu.gif)

## Setup

- build: `uv venv && uv pip install -e .` or `uv build` in short.
- setup zendesk credentials in `.env` file, refer to [.env.example](.env.example).
- configure in Claude desktop:

```json
{
  "mcpServers": {
      "zendesk": {
          "command": "uv",
          "args": [
              "--directory",
              "/path/to/zendesk-mcp-server",
              "run",
              "zendesk"
          ]
      }
  }
}
```

### Docker

You can containerize the server if you prefer an isolated runtime:

1. Copy `.env.example` to `.env` and fill in your Zendesk credentials. Keep this file outside version control.
2. Build the image:

   ```bash
   docker build -t zendesk-mcp-server .
   ```

3. Run the server, providing the environment file:

   ```bash
   docker run --rm --env-file /path/to/.env zendesk-mcp-server
   ```

   Add `-i` when wiring the container to MCP clients over STDIN/STDOUT (Claude Code uses this mode). For daemonized runs, add `-d --name zendesk-mcp`.

The image installs dependencies from `requirements.lock`, drops privileges to a non-root user, and expects configuration exclusively via environment variables.

#### Claude MCP Integration

To use the Dockerized server from Claude Code/Desktop, add an entry to Claude Code's `settings.json` similar to:

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "/usr/local/bin/docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "--env-file",
        "/path/to/zendesk-mcp-server/.env",
        "zendesk-mcp-server"
      ]
    }
  }
}
```

Adjust the paths to match your environment. After saving the file, restart Claude for the new MCP server to be detected.

#### Custom Handler Mappings In Claude

If you want Claude to treat a phrase like `triage` as a direct instruction to run a specific MCP tool, the most reliable approach is to define that mapping in Claude's system prompt or user preferences.

Example instruction:

> "When I say triage, always call the `zendesk:scan_tickets_in_trouble` MCP tool. Do not interpret it as a general concept; treat it as a direct command to invoke that handler."

For personal use, the simplest place to add this is in Claude.ai under `Settings -> Profile -> User Preferences`, so the mapping applies across all of your conversations.

## Resources

- `zendesk://knowledge-base`
  - Returns Zendesk Help Center sections and articles as JSON.
  - Includes metadata with section count and total article count.
  - Cached for 1 hour server-side.

## Prompts

### analyze-ticket (`ticket_id`)

Returns the QA analysis rubric template for a specific ticket id.

### draft-ticket-response (`ticket_id`)

Returns a response-drafting prompt for a specific ticket.

### ticket-title-review-policy

Returns the ticket-title naming policy template.

Policy highlights enforced by the prompt:
- Preferred title shape is `Customer | Context | Issue`.
- Accepted context segment can be an OS/version, feature, integration/tool, or a generic platform marker like `Platform` / `OS`.
- `Trial` may appear before the customer name.
- Case-only differences are ignored.
- A title is invalid when key context is missing, ambiguous, or the segmented structure is unclear.
- Reviews must not invent missing facts; missing information must be called out explicitly.
- If a ticket is waiting on the customer, `Status With` must be `Customer` or the review should be marked invalid.
- If an escalation field is populated, the ticket should only be treated as properly solved when the customer explicitly confirmed the solution worked.

### review-ticket-title (`ticket_id`)

Returns the title-review policy plus instructions for reviewing one ticket title.

Expected output format:
- `Validation: VALID` or `Validation: INVALID`
- `Reason: ...`
- `Suggested Title: ...` only when invalid
- Batch reviews also require `Summary: <count valid> valid, <count invalid> invalid`

## Tools

### get_ticket (`ticket_id`)

Fetch one ticket with normalized custom field values and tags.

### get_ticket_summary (`ticket_id`)

Fetch one ticket and return a compact display-ready markdown summary.

### review_ticket (`ticket_id`)

Returns a full review packet:
- ticket payload
- ticket comments (including attachment metadata)
- analysis rubric text

Review rubric now requires reporting escalation timing, including:
- `Time to escalation from ticket creation`
- for `crash_detected` tickets, explicit escalation-latency reporting (or `Not found` + process gap if timestamp is missing)
- for `crash_detected` tickets, missing crash identification, missing stacktrace handling, or untimely/unverifiable escalation are hard-fail conditions that require a compliance score of `0`

The review rubric also enforces these evidence and process rules:
- Use only ticket fields, ticket comments, and attachment metadata as evidence. If something cannot be found, report `Not found`.
- Timeline output must include: opened, first agent response, crash identified, stacktrace requested, escalated, time to escalation, solution built, solution delivered, and customer acknowledgement.
- Attachment evidence output must explicitly list crash-related attachments, stacktraces, replication videos, and other crash artifacts by filename when present.
- Tom Tovar participation must be reported from `tom_tovar_*` metadata and comments.
- Every timeline/compliance statement should identify the evidence source and author.
- Email-chain preambles and forwarded-history text must not be used to justify or excuse agent handling.
- Delay justification is valid only when the agent explicitly documented the reason in actions/internal notes.
- Customer-side context in the opening message cannot be used to excuse agent delay.
- Escalated tickets cannot be treated as customer-acknowledged unless the customer explicitly confirmed the fix worked.
- For `crash_detected` / `anr_yes` tickets:
  - stacktrace evidence counts only when comments or attachments explicitly contain crash-log/stacktrace evidence
  - if stacktrace evidence is missing, the assigned engineer must explicitly request it
  - the first stacktrace/crash-log request should happen within 1 hour of crash identification
  - escalation timing must always be calculated from ticket creation, and missing escalation timestamps must be flagged as a process gap
  - if crash/ANR handling is not explicitly covered in Timeline or Process Review, the compliance score must be `0`
  - if no stacktrace evidence exists and no explicit request exists, the compliance score must be `0`
  - if the first stacktrace request is more than 1 hour late, the compliance score must be `0`
  - if escalation is more than 1 hour late or cannot be verified, the compliance score must be `0`

### get_tickets

Fetch ticket list with pagination and optional filters.

- Input:
  - `page` (integer, optional): Page number (defaults to 1)
  - `per_page` (integer, optional): Number of tickets per page, max 100 (defaults to 25)
  - `sort_by` (string, optional): Field to sort by - created_at, updated_at, priority, or status (defaults to created_at)
  - `sort_order` (string, optional): Sort order - asc or desc (defaults to desc)
  - `agent` (string, optional): Assignee filter. Can be an id, email, or name
  - `organization` (string, optional): Organization name filter
  - `updated_since` (string, optional): ISO date/datetime filter
  - `last_hours` (integer, optional): Relative filter. Example: `5` means tickets updated in the last 5 hours
  - `created_last_hours` (integer, optional): Relative filter. Example: `4` means tickets created in the last 4 hours
  - `stale_hours` (integer, optional): Relative stale filter. Example: `24` means tickets not updated in the last 24 hours
  - `include_solved` (boolean, optional): Include solved/closed tickets when using `stale_hours`
  - `exclude_internal` (boolean, optional): Exclude tickets tagged `internal` from results

- Output:
  - Structured result (`structured_output=True`) with ticket list + pagination + optional `filters`.
  - Each ticket includes stale age fields: `stale_age_hours`, `stale_age_days`.

### get_important_tickets_today

Find tickets that matter today based on current attention needs, not just creation time.

- What it does:
  - fetches tickets updated in the last `recent_activity_hours`
  - fetches stale tickets older than `stale_hours`
  - de-duplicates the combined candidate set
  - runs the existing ticket trouble assessment on each candidate
  - returns a ranked structured list of tickets that are most likely to need attention now

- Input:
  - `recent_activity_hours` (integer, optional): Include tickets updated in the last N hours. Default `24`
  - `stale_hours` (integer, optional): Also include tickets not updated in the last N hours. Default `8`
  - `per_page` (integer, optional): Max tickets to fetch from each candidate query, max `100`. Default `50`
  - `agent` (string, optional): Assignee filter. Can be an id, email, or name
  - `organization` (string, optional): Organization name filter
  - `exclude_internal` (boolean, optional): Exclude tickets tagged `internal`. Default `true`
  - `initial_response_sla_minutes` (integer, optional): SLA threshold for first public agent response. Default `60`
  - `high_priority_stale_hours` (integer, optional): Trouble-assessment threshold for stale escalated high-priority or stale support-owned tickets. Default `8`

- Output:
  - Structured result with:
    - `filters`
    - `candidate_count`
    - `in_trouble_count`
    - `ticket_list_markdown`
    - `tickets`
  - Each ticket is returned as a `TicketTroubleAssessment`, including:
    - `ticket_id`, `ticket_url`, `ticket_link`
    - `subject`, `status`, `priority`
    - `is_escalated`
    - `priority_interpretation`
    - `in_trouble`
    - `risk_score`
    - `flags`
    - `production_impact`
    - `crash_attachment_summary`
    - recent comment notes

- Recommended call:

```json
{
  "name": "get_important_tickets_today",
  "arguments": {
    "recent_activity_hours": 24,
    "stale_hours": 8,
    "per_page": 50,
    "exclude_internal": true
  }
}
```

### search_tickets_by_text

Search ticket content by phrase, with optional narrowing.

- Input:
  - `phrase` (string, required)
  - `page`, `per_page`, `sort_by`, `sort_order`
  - `organization` (optional)
  - `updated_since` (optional)
  - `updated_before` (optional)
  - `last_days` (optional shorthand that maps to `updated_since`)
  - `status` (optional)
  - `include_solved` (optional)
  - `exclude_internal` (optional)
  - `comment_author` (optional, id/name/email)

- Output:
  - Structured result with tickets, built search query string, filters, and pagination fields.
  - Each returned ticket includes `match_type`:
    - `exact` for the quoted phrase search
    - `partial` when the exact search returned 0 and the server fell back to a broader token search
  - Search metadata includes:
    - `exact_query`
    - `partial_query`
    - `search_mode`
    - `exact_count`
    - `partial_fallback_used`
    - `partial_fallback_reason`
  - Partial fallback is intentionally skipped for very short/common phrases so searches like `to` do not return an unhelpful flood of tickets.

### scan_tickets_in_trouble

Scan non-solved tickets created in the last N hours and flag likely QA/process issues.

- Input:
  - `created_last_hours` (default `4`)
  - `per_page` (default `50`, max `100`)
  - `exclude_internal` (default `true`)
  - `initial_response_sla_minutes` (default `60`)
  - `high_priority_stale_hours` (default `8`)

- Checks include:
  - title format
  - production-user-impact detection
  - crash-ticket process gaps and missing crash tags
  - required status/custom-field completeness
- late or missing initial public response
- scheduled/requested meetings without later public SDE call-summary notes
- customer public comment without timely follow-up
  - solved/closed tickets without explicit customer confirmation
  - stale high-priority escalations and stale support-owned tickets

Current flag conditions include:
- `title_incorrect`: subject does not match the expected `Customer | Context | Issue` structure.
- `production_user_impact`: ticket text/comments indicate a live production issue affecting real users/customers.
- `status_fields_incomplete`: one or more of `Status With`, `Support Stage`, or `Release Stage` is missing.
- `missing_initial_response`: no public agent reply after the configured first-response SLA, unless the first comment was internal.
- `late_initial_response`: first public agent reply exceeded the configured first-response SLA.
- `meeting_summary_missing`: a meeting/call was requested or scheduled, but no later summary notes from the assigned SDE/agent were found after the meeting should have occurred. Public comments and private/internal notes both count.
- `customer_comment_no_response`: a customer public comment did not receive a public agent follow-up within the configured SLA; also used when a ticket stays open for days after a customer says no response is needed.
- `solved_without_customer_confirmation`: ticket is solved/closed without explicit customer confirmation in public comments.
- `high_priority_no_recent_updates`: escalated `high`/`urgent` ticket has been stale longer than the configured threshold.
- `support_owned_no_recent_updates`: non-escalated support-owned ticket has been stale longer than the configured threshold.
- `crash_tag_missing`: ticket text suggests a crash, but it lacks `crash_detected` / `anr_yes`.
- `crash_tag_missing_unreviewed_attachment_evidence`: crash-related attachments exist, but the ticket lacks `crash_detected` / `anr_yes` and also lacks the `crash_reviewed` override tag.
- `crash_process_gap`: crash/ANR ticket has neither stacktrace evidence nor an explicit request for crash logs.
- `late_stacktrace_request`: crash/ANR ticket requested stacktrace evidence more than 60 minutes after ticket creation when evidence was not already present.

### scan_crash_tickets_in_trouble

Scan open, non-internal tickets with a crash-related tag and flag likely QA/process issues, without a created-date window.

- Input:
  - `tag` (default `crash_detected`)
  - `max_results` (default `250`, max `1000`)
  - `per_page` (default `100`, max `100`)
  - `exclude_internal` (default `true`)
  - `initial_response_sla_minutes` (default `60`)
  - `high_priority_stale_hours` (default `8`)

- Behavior:
  - searches `tag=<value>` with `status:open`
  - excludes `pending`, `solved`, and `closed`
  - excludes `internal` when `exclude_internal=true`

- Output:
  - Structured result with `tag`, `scanned_count`, `in_trouble_count`, `total_matches`, `retrieved_count`, `truncated`, `ticket_list_markdown`, and per-ticket trouble assessments.

### sample_solved_tickets_for_agent

Randomly sample solved tickets for a specific agent in a date window.

- Input:
  - `agent` (required)
  - `solved_after` (required, inclusive date)
  - `solved_before` (required, exclusive date)
  - `count` (default `4`)
  - `exclude_api_created` (default `false`)
  - `seed` (optional for repeatable sampling)
  - `max_pool` (default `250`)

- Output:
  - Structured sample result with sampled tickets, pool/retrieval stats, truncation flag, and exclusion counts.

### review_random_solved_tickets_for_agent

Samples solved tickets and returns a combined ticket-QA input packet for all sampled tickets.

- Input:
  - Same inputs as `sample_solved_tickets_for_agent`

- Output:
  - Structured result including sampled ticket ids and `review_input` (rubric + ticket evidence bundle).
  - Also highlights production-impact tickets separately via `production_ticket_ids`, `production_ticket_links`, and `production_ticket_count`.

### get_ticket_comments (`ticket_id`)

Fetch all comments for a ticket, including attachment metadata:
- `id`, `file_name`, `content_type`, `size`, `inline`

### create_ticket_comment

Create a new comment on an existing ticket.

- Input:
  - `ticket_id` (integer): The ID of the ticket to comment on
  - `comment` (string): Comment body
  - `public` (boolean, optional): Whether the comment should be public (defaults to true)

### create_ticket

Create a new Zendesk ticket.

- Input:
  - `subject` (string): Ticket subject
  - `description` (string): Ticket description
  - `requester_id` (integer, optional)
  - `assignee_id` (integer, optional)
  - `priority` (string, optional): one of `low`, `normal`, `high`, `urgent`
  - `type` (string, optional): one of `problem`, `incident`, `question`, `task`
  - `tags` (array[string], optional)
  - `custom_fields` (array[object], optional)

### update_ticket

Update fields on an existing ticket.

- Input:
  - `ticket_id` (integer): The ID of the ticket to update
  - `subject` (string, optional)
  - `status` (string, optional): one of `new`, `open`, `pending`, `on-hold`, `solved`, `closed`
  - `priority` (string, optional): one of `low`, `normal`, `high`, `urgent`
  - `type` (string, optional)
  - `assignee_id` (integer, optional)
  - `requester_id` (integer, optional)
  - `tags` (array[string], optional)
  - `custom_fields` (array[object], optional)
  - `due_at` (string, optional): ISO8601 datetime

### get_ticket_fields

Lists Zendesk ticket fields with:
- `id`
- `title`
- `type`
- `active`
