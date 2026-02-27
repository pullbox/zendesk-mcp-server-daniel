from typing import Dict, Any, List, Optional
import json
import urllib.request
import urllib.parse
import base64

from zenpy import Zenpy
from zenpy.lib.api_objects import Comment
from zenpy.lib.api_objects import Ticket as ZenpyTicket
from datetime import datetime, timedelta, timezone


class ZendeskClient:
    def __init__(self, subdomain: str, email: str, token: str):
        """
        Initialize the Zendesk client using zenpy lib and direct API.
        """
        self.client = Zenpy(
            subdomain=subdomain,
            email=email,
            token=token
        )

        # For direct API calls
        self.subdomain = subdomain
        self.email = email
        self.token = token
        self.base_url = f"https://{subdomain}.zendesk.com/api/v2"
        # Create basic auth header
        credentials = f"{email}/token:{token}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode('ascii')
        self.auth_header = f"Basic {encoded_credentials}"

    def get_ticket(self, ticket_id: int) -> Dict[str, Any]:
        """
        Query a ticket by its ID
        """
        try:
            ticket = self.client.tickets(id=ticket_id)
            return {
                'id': ticket.id,
                'subject': ticket.subject,
                'description': ticket.description,
                'status': ticket.status,
                'priority': ticket.priority,
                'created_at': str(ticket.created_at),
                'updated_at': str(ticket.updated_at),
                'requester_id': ticket.requester_id,
                'assignee_id': ticket.assignee_id,
                'organization_id': ticket.organization_id
            }
        except Exception as e:
            raise Exception(f"Failed to get ticket {ticket_id}: {str(e)}")

    def get_ticket_comments(self, ticket_id: int) -> List[Dict[str, Any]]:
        """
        Get all comments for a specific ticket.
        """
        try:
            comments = self.client.tickets.comments(ticket=ticket_id)
            return [{
                'id': comment.id,
                'author_id': comment.author_id,
                'body': comment.body,
                'html_body': comment.html_body,
                'public': comment.public,
                'created_at': str(comment.created_at)
            } for comment in comments]
        except Exception as e:
            raise Exception(f"Failed to get comments for ticket {ticket_id}: {str(e)}")

    def post_comment(self, ticket_id: int, comment: str, public: bool = True) -> str:
        """
        Post a comment to an existing ticket.
        """
        try:
            ticket = self.client.tickets(id=ticket_id)
            ticket.comment = Comment(
                html_body=comment,
                public=public
            )
            self.client.tickets.update(ticket)
            return comment
        except Exception as e:
            raise Exception(f"Failed to post comment on ticket {ticket_id}: {str(e)}")

    ## Old version 
    # def get_tickets(self, page: int = 1, per_page: int = 25, sort_by: str = 'created_at', sort_order: str = 'desc') -> Dict[str, Any]:

    ## new version allowing to filter by org and agent
    def get_tickets(
        self,
        page: int = 1,
        per_page: int = 25,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        agent: Optional[str] = None,
        organization: Optional[str] = None,
        updated_since: Optional[str] = None,
        last_hours: Optional[int] = None, # NEW
        stale_hours: Optional[int] = None, # NEW
        include_solved: bool = False, # NEW
    ) -> Dict[str, Any]:
        """
        Get tickets with optional filtering.

        - If agent/organization/updated_since filters are provided, uses Zendesk Search API:
            GET /api/v2/search.json?query=type:ticket ...
        because /tickets.json doesn't support these filters.
        - If no filters are provided, uses /tickets.json (original behavior).

        Args:
            page: Page number (1-based)
            per_page: Number of tickets per page (max 100)
            sort_by: Field to sort by (created_at, updated_at, priority, status) for /tickets.json
            sort_order: Sort order (asc or desc) for /tickets.json
            agent: Optional assignee filter (id, email, or name)
            organization: Optional organization name filter (partial name ok)
            updated_since: Optional ISO date/datetime filter for search: e.g. 2026-02-26 or 2026-02-26T10:00:00Z

        Returns:
            Dict containing tickets and pagination info
        """
        try:
            per_page = min(per_page, 100)

            # If any filters are present, use the Search API
            # NEW UPDATED
            if agent or organization or updated_since or last_hours is not None or stale_hours is not None:
                query_parts = ["type:ticket"]

                if agent:
                    agent_str = str(agent).strip()
                    # If numeric, use assignee_id for deterministic filtering
                    if agent_str.isdigit():
                        query_parts.append(f"assignee_id:{agent_str}")
                    else:
                        query_parts.append(f'assignee:"{agent_str}"')

                if organization:
                    org_str = str(organization).strip()
                    query_parts.append(f'organization:"{org_str}"')

                # NEW: stale detector - show tickets NOT updated recently
                # stale_hours=24  => updated < (now - 24h)
                updated_before = None
                if stale_hours is not None:
                    dt = datetime.now(timezone.utc) - timedelta(hours=int(stale_hours))
                    updated_before = dt.isoformat().replace("+00:00", "Z")

                # If using stale detection, default to open-ish tickets unless caller requests otherwise
                # This keeps out solved/closed tickets
                if stale_hours is not None and not include_solved:
                    query_parts.append("status<solved")

                # Apply updated constraints
                if last_hours is not None:
                    dt = datetime.now(timezone.utc) - timedelta(hours=int(last_hours))
                    updated_since = dt.isoformat().replace("+00:00", "Z")

                if updated_before:
                    query_parts.append(f"updated<{updated_before}")


                if updated_since:
                    # Zendesk Search supports updated>YYYY-MM-DD and updated>YYYY-MM-DDTHH:MM:SSZ
                    since_str = str(updated_since).strip()
                    query_parts.append(f"updated>{since_str}")



                query = " ".join(query_parts)

                params = {
                    "query": query,
                    "page": str(page),
                    "per_page": str(per_page),
                }

                # NOTE: Some Zendesk instances reject sort params on search.
                # If you get HTTP 400, remove these two lines.
                params["sort_by"] = sort_by
                params["sort_order"] = sort_order

                url = f"{self.base_url}/search.json?{urllib.parse.urlencode(params)}"

                req = urllib.request.Request(url)
                req.add_header("Authorization", self.auth_header)
                req.add_header("Content-Type", "application/json")

                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode())

                results = data.get("results", [])

                ticket_list = []
                for item in results:
                    # Guard: only tickets
                    if item.get("result_type") not in (None, "ticket"):
                        continue

                    ticket_list.append({
                        "id": item.get("id"),
                        "subject": item.get("subject"),
                        "status": item.get("status"),
                        "priority": item.get("priority"),
                        "description": item.get("description"),
                        "created_at": item.get("created_at"),
                        "updated_at": item.get("updated_at"),
                        "requester_id": item.get("requester_id"),
                        "assignee_id": item.get("assignee_id"),
                        "organization_id": item.get("organization_id"),
                    })

                return {
                    "tickets": ticket_list,
                    "page": page,
                    "per_page": per_page,
                    "count": len(ticket_list),
                    "sort_by": sort_by,
                    "sort_order": sort_order,
                    "filters": {
                        "agent": agent,
                        "organization": organization,
                        "updated_since": updated_since,
                        "last_hours": last_hours,
                        "stale_hours": stale_hours,
                        "include_solved": include_solved,
                    },
                    "has_more": data.get("next_page") is not None,
                    "next_page": page + 1 if data.get("next_page") else None,
                    "previous_page": page - 1 if page > 1 else None,
                }

            # No filters -> original /tickets.json behavior
            params = {
                "page": str(page),
                "per_page": str(per_page),
                "sort_by": sort_by,
                "sort_order": sort_order,
            }
            url = f"{self.base_url}/tickets.json?{urllib.parse.urlencode(params)}"

            req = urllib.request.Request(url)
            req.add_header("Authorization", self.auth_header)
            req.add_header("Content-Type", "application/json")

            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())

            tickets_data = data.get("tickets", [])

            ticket_list = []
            for ticket in tickets_data:
                ticket_list.append({
                    "id": ticket.get("id"),
                    "subject": ticket.get("subject"),
                    "status": ticket.get("status"),
                    "priority": ticket.get("priority"),
                    "description": ticket.get("description"),
                    "created_at": ticket.get("created_at"),
                    "updated_at": ticket.get("updated_at"),
                    "requester_id": ticket.get("requester_id"),
                    "assignee_id": ticket.get("assignee_id"),
                    "organization_id": ticket.get("organization_id"),
                })

            return {
                "tickets": ticket_list,
                "page": page,
                "per_page": per_page,
                "count": len(ticket_list),
                "sort_by": sort_by,
                "sort_order": sort_order,
                "has_more": data.get("next_page") is not None,
                "next_page": page + 1 if data.get("next_page") else None,
                "previous_page": page - 1 if data.get("previous_page") and page > 1 else None,
            }

        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to get tickets: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to get tickets: {str(e)}")

    def get_all_articles(self) -> Dict[str, Any]:
        """
        Fetch help center articles as knowledge base.
        Returns a Dict of section -> [article].
        """
        try:
            # Get all sections
            sections = self.client.help_center.sections()

            # Get articles for each section
            kb = {}
            for section in sections:
                articles = self.client.help_center.sections.articles(section.id)
                kb[section.name] = {
                    'section_id': section.id,
                    'description': section.description,
                    'articles': [{
                        'id': article.id,
                        'title': article.title,
                        'body': article.body,
                        'updated_at': str(article.updated_at),
                        'url': article.html_url
                    } for article in articles]
                }

            return kb
        except Exception as e:
            raise Exception(f"Failed to fetch knowledge base: {str(e)}")

    def create_ticket(
        self,
        subject: str,
        description: str,
        requester_id: int | None = None,
        assignee_id: int | None = None,
        priority: str | None = None,
        type: str | None = None,
        tags: List[str] | None = None,
        custom_fields: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """
        Create a new Zendesk ticket using Zenpy and return essential fields.

        Args:
            subject: Ticket subject
            description: Ticket description (plain text). Will also be used as initial comment.
            requester_id: Optional requester user ID
            assignee_id: Optional assignee user ID
            priority: Optional priority (low, normal, high, urgent)
            type: Optional ticket type (problem, incident, question, task)
            tags: Optional list of tags
            custom_fields: Optional list of dicts: {id: int, value: Any}
        """
        try:
            ticket = ZenpyTicket(
                subject=subject,
                description=description,
                requester_id=requester_id,
                assignee_id=assignee_id,
                priority=priority,
                type=type,
                tags=tags,
                custom_fields=custom_fields,
            )
            created_audit = self.client.tickets.create(ticket)
            # Fetch created ticket id from audit
            created_ticket_id = getattr(getattr(created_audit, 'ticket', None), 'id', None)
            if created_ticket_id is None:
                # Fallback: try to read id from audit events
                created_ticket_id = getattr(created_audit, 'id', None)

            # Fetch full ticket to return consistent data
            created = self.client.tickets(id=created_ticket_id) if created_ticket_id else None

            return {
                'id': getattr(created, 'id', created_ticket_id),
                'subject': getattr(created, 'subject', subject),
                'description': getattr(created, 'description', description),
                'status': getattr(created, 'status', 'new'),
                'priority': getattr(created, 'priority', priority),
                'type': getattr(created, 'type', type),
                'created_at': str(getattr(created, 'created_at', '')),
                'updated_at': str(getattr(created, 'updated_at', '')),
                'requester_id': getattr(created, 'requester_id', requester_id),
                'assignee_id': getattr(created, 'assignee_id', assignee_id),
                'organization_id': getattr(created, 'organization_id', None),
                'tags': list(getattr(created, 'tags', tags or []) or []),
            }
        except Exception as e:
            raise Exception(f"Failed to create ticket: {str(e)}")

    def update_ticket(self, ticket_id: int, **fields: Any) -> Dict[str, Any]:
        """
        Update a Zendesk ticket with provided fields using Zenpy.

        Supported fields include common ticket attributes like:
        subject, status, priority, type, assignee_id, requester_id,
        tags (list[str]), custom_fields (list[dict]), due_at, etc.
        """
        try:
            # Load the ticket, mutate fields directly, and update
            ticket = self.client.tickets(id=ticket_id)
            for key, value in fields.items():
                if value is None:
                    continue
                setattr(ticket, key, value)

            # This call returns a TicketAudit (not a Ticket). Don't read attrs from it.
            self.client.tickets.update(ticket)

            # Fetch the fresh ticket to return consistent data
            refreshed = self.client.tickets(id=ticket_id)

            return {
                'id': refreshed.id,
                'subject': refreshed.subject,
                'description': refreshed.description,
                'status': refreshed.status,
                'priority': refreshed.priority,
                'type': getattr(refreshed, 'type', None),
                'created_at': str(refreshed.created_at),
                'updated_at': str(refreshed.updated_at),
                'requester_id': refreshed.requester_id,
                'assignee_id': refreshed.assignee_id,
                'organization_id': refreshed.organization_id,
                'tags': list(getattr(refreshed, 'tags', []) or []),
            }
        except Exception as e:
            raise Exception(f"Failed to update ticket {ticket_id}: {str(e)}")