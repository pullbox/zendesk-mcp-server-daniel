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

- zendesk://knowledge-base, get access to the whole help center articles.

## Prompts

### analyze-ticket

Analyze a Zendesk ticket and provide a detailed analysis of the ticket.

### draft-ticket-response

Draft a response to a Zendesk ticket.

## Tools

### get_tickets

Fetch the latest tickets with pagination support

- Input:
  - `page` (integer, optional): Page number (defaults to 1)
  - `per_page` (integer, optional): Number of tickets per page, max 100 (defaults to 25)
  - `sort_by` (string, optional): Field to sort by - created_at, updated_at, priority, or status (defaults to created_at)
  - `sort_order` (string, optional): Sort order - asc or desc (defaults to desc)
  - `agent` (string, optional): Assignee filter. Can be an id, email, or name
  - `organization` (string, optional): Organization name filter
  - `updated_since` (string, optional): ISO date/datetime filter
  - `last_hours` (integer, optional): Relative filter. Example: `5` means tickets updated in the last 5 hours
  - `stale_hours` (integer, optional): Relative stale filter. Example: `24` means tickets not updated in the last 24 hours
  - `include_solved` (boolean, optional): Include solved/closed tickets when using `stale_hours`

- Client return shape: `ZendeskClient.get_tickets(...)` returns a JSON object, not a bare array.

```json
{
  "tickets": [
    {
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
      "custom_fields": {
        "Team": "billing"
      }
    }
  ],
  "page": 1,
  "per_page": 25,
  "count": 1,
  "sort_by": "created_at",
  "sort_order": "desc",
  "filters": {
    "agent": null,
    "organization": null,
    "updated_since": "2026-03-02T10:30:00+00:00",
    "last_hours": 5,
    "stale_hours": null,
    "include_solved": false
  },
  "has_more": false,
  "next_page": null,
  "previous_page": null
}
```

- Notes:
  - `tickets` is always an array of ticket objects.
  - `count` is the number of ticket objects returned in the current response page.
  - `filters` is included for search-based calls such as `last_hours`, `stale_hours`, `agent`, `organization`, or `updated_since`.
  - `custom_fields` is always an object keyed by field name when field metadata is available. It is not returned as the raw Zendesk list format.
  - Search results that are not tickets are filtered out before this object is returned.
  - When no filters are supplied and the client falls back to `/tickets.json`, the top-level shape is the same except `filters` is not included.

- MCP server return shape: the `get_tickets` tool wraps that same object inside a single MCP text response whose `text` value is JSON.

```json
[
  {
    "type": "text",
    "text": "{\n  \"tickets\": [...],\n  \"page\": 1,\n  \"per_page\": 25,\n  \"count\": 1,\n  \"sort_by\": \"created_at\",\n  \"sort_order\": \"desc\",\n  \"filters\": {\n    \"last_hours\": 5\n  },\n  \"has_more\": false,\n  \"next_page\": null,\n  \"previous_page\": null\n}"
  }
]
```

- Tests covering the expected format live in [`src/zendesk_mcp_server/ticket_test.py`](./src/zendesk_mcp_server/ticket_test.py).

### get_ticket

Retrieve a Zendesk ticket by its ID

- Input:
  - `ticket_id` (integer): The ID of the ticket to retrieve

### get_ticket_comments

Retrieve all comments for a Zendesk ticket by its ID

- Input:
  - `ticket_id` (integer): The ID of the ticket to get comments for

### create_ticket_comment

Create a new comment on an existing Zendesk ticket

- Input:
  - `ticket_id` (integer): The ID of the ticket to comment on
  - `comment` (string): The comment text/content to add
  - `public` (boolean, optional): Whether the comment should be public (defaults to true)

### create_ticket

Create a new Zendesk ticket

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

Update fields on an existing Zendesk ticket (e.g., status, priority, assignee)

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
