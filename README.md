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

### review-ticket-title (`ticket_id`)

Returns the title-review policy plus instructions for reviewing one ticket title.

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

### scan_tickets_in_trouble

Scan tickets created in the last N hours and flag likely QA/process issues.

- Input:
  - `created_last_hours` (default `4`)
  - `per_page` (default `50`, max `100`)
  - `exclude_internal` (default `true`)
  - `initial_response_sla_minutes` (default `60`)
  - `high_priority_stale_hours` (default `8`)

- Checks include:
  - title format
  - crash-ticket process gaps
  - required status/custom-field completeness
  - late or missing initial response
  - customer public comment without follow-up
  - solved/closed without customer confirmation
  - high-priority tickets with stale updates

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

Samples solved tickets and returns a combined review input packet for all sampled tickets.

- Input:
  - Same inputs as `sample_solved_tickets_for_agent`

- Output:
  - Structured result including sampled ticket ids and `review_input` (rubric + ticket evidence bundle).

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
