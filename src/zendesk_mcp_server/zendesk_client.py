import datetime as dtlib
from typing import Dict, Any, List, Optional
import json
import urllib.error
import urllib.request
import base64
import logging

from zendesk_mcp_server.infrastructure.zendesk.tickets_repository import TicketsRepository

logger = logging.getLogger("zendesk-mcp-client")
_log_handlers = [logging.StreamHandler()]

try:
    _log_handlers.append(logging.FileHandler("zendesk-mcp.log"))
except OSError:
    logger.warning("File logging is unavailable; continuing with stdout logging only.")

logging.basicConfig(
    level=logging.INFO,
    handlers=_log_handlers,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

from zenpy import Zenpy
from zenpy.lib.api_objects import Comment
from zenpy.lib.api_objects import Ticket as ZenpyTicket
from datetime import datetime, timezone


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
        self.agent_ticket_base_url = f"https://{subdomain}.zendesk.com/agent/tickets"
        # Create basic auth header
        credentials = f"{email}/token:{token}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode('ascii')
        self.auth_header = f"Basic {encoded_credentials}"
        self.tickets_repository = TicketsRepository(
            base_url=self.base_url,
            json_get=lambda url: self._json_get(url),
            build_ticket_list_item=lambda ticket, now: self._build_ticket_list_item(ticket, now),
            timestamp_formatter=lambda value: self._zendesk_ts(value),
        )

    def _json_get(self, url: str, timeout: int = 30) -> Dict[str, Any]:
        req = urllib.request.Request(url)
        req.add_header("Authorization", self.auth_header)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode())

    def get_ticket_fields(self) -> List[Dict[str, Any]]:
        """
        Fetch all ticket fields (standard + custom) from Zendesk.
        Useful for discovering custom field IDs and names.
        """
        try:
            url = f"{self.base_url}/ticket_fields.json"
            logger.info("Fetching Zendesk ticket fields")
            data = self._json_get(url)
            logger.info("Fetched Zendesk ticket fields successfully")
            return [
                {
                    "id": f.get("id"),
                    "title": f.get("title"),
                    "type": f.get("type"),
                    "active": f.get("active"),
                }
                for f in data.get("ticket_fields", [])
            ]
        except Exception as e:
            logger.error(f"Failed to get Zendesk ticket fields: {e}")
            raise Exception(f"Failed to get ticket fields: {str(e)}")

    def get_ticket_field_definitions(self) -> List[Dict[str, Any]]:
        """
        Fetch full ticket field definitions so option metadata can be resolved dynamically.
        """
        try:
            url = f"{self.base_url}/ticket_fields.json"
            logger.info("Fetching Zendesk ticket field definitions")
            data = self._json_get(url)
            logger.info("Fetched Zendesk ticket field definitions successfully")
            return data.get("ticket_fields", [])
        except Exception as e:
            logger.error(f"Failed to get Zendesk ticket field definitions: {e}")
            raise Exception(f"Failed to get ticket field definitions: {str(e)}")

    def get_ticket_field_options(self, ticket_field_id: int) -> List[Dict[str, Any]]:
        """
        Fetch selectable options for a specific ticket field.
        """
        try:
            url = f"{self.base_url}/ticket_fields/{ticket_field_id}/options.json"
            logger.info(f"Fetching Zendesk ticket field options for field {ticket_field_id}")
            data = self._json_get(url)
            logger.info(f"Fetched Zendesk ticket field options for field {ticket_field_id} successfully")
            return data.get("custom_field_options", [])
        except Exception as e:
            logger.error(f"Failed to get Zendesk ticket field options for field {ticket_field_id}: {e}")
            raise Exception(f"Failed to get ticket field options for field {ticket_field_id}: {str(e)}")

    def _get_field_map(self) -> Dict[int, str]:
        """
        Return a cached id->title mapping for all ticket fields.
        Re-fetches on each ZendeskClient instance (not across restarts).
        Falls back to an empty map if the ticket_fields API is unavailable.
        """
        if not hasattr(self, "_field_map_cache"):
            try:
                fields = self.get_ticket_fields()
                self._field_map_cache = {f["id"]: f["title"] for f in fields}
            except Exception as e:
                logger.warning(f"Could not load ticket field map (custom fields will show IDs): {e}")
                self._field_map_cache = {}
        return self._field_map_cache

    def _resolve_custom_fields(self, raw: list) -> Dict[str, Any]:
        """
        Convert a raw custom_fields list [{id, value}, ...] from the REST API
        into a {field_title: value} dict, omitting null values.
        """
        if not raw:
            return {}
        field_map = self._get_field_map()
        return {
            field_map.get(cf["id"], str(cf["id"])): cf["value"]
            for cf in raw
            if cf.get("value") is not None
        }

    def get_ticket(self, ticket_id: int) -> Dict[str, Any]:
        """
        Query a ticket by its ID, including resolved custom field values.
        """
        try:
            logger.info(f"Fetching Zendesk ticket {ticket_id}")
            data = self._json_get(f"{self.base_url}/tickets/{ticket_id}.json")
            ticket = data.get("ticket", {})
            custom_fields = self._resolve_custom_fields(ticket.get("custom_fields", []))
            result = {
                'id': ticket.get('id'),
                'subject': ticket.get('subject'),
                'description': ticket.get('description'),
                'status': ticket.get('status'),
                'priority': ticket.get('priority'),
                'created_at': ticket.get('created_at'),
                'updated_at': ticket.get('updated_at'),
                'requester_id': ticket.get('requester_id'),
                'assignee_id': ticket.get('assignee_id'),
                'organization_id': ticket.get('organization_id'),
                'tags': ticket.get('tags', []),
                'custom_fields': custom_fields,
            }
            logger.info(f"Fetched Zendesk ticket {ticket_id} successfully")
            return result
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            logger.error(f"Failed to fetch Zendesk ticket {ticket_id}: HTTP {e.code} - {e.reason}. {error_body}")
            raise Exception(f"Failed to get ticket {ticket_id}: HTTP {e.code} - {e.reason}. {error_body}")
        except urllib.error.URLError as e:
            logger.error(f"Failed to fetch Zendesk ticket {ticket_id}: {e}")
            raise Exception(f"Failed to get ticket {ticket_id}: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to fetch Zendesk ticket {ticket_id}: {e}")
            raise Exception(f"Failed to get ticket {ticket_id}: {str(e)}")

    def get_ticket_comments(self, ticket_id: int) -> List[Dict[str, Any]]:
        """
        Get all comments for a specific ticket.
        """
        try:
            logger.info(f"Fetching Zendesk comments for ticket {ticket_id}")
            comments: List[Dict[str, Any]] = []
            url = f"{self.base_url}/tickets/{ticket_id}/comments.json"

            while url:
                data = self._json_get(url)
                for comment in data.get("comments", []):
                    attachments = []
                    for attachment in comment.get("attachments", []) or []:
                        attachments.append(
                            {
                                "id": attachment.get("id"),
                                "file_name": attachment.get("file_name"),
                                "content_type": attachment.get("content_type"),
                                "size": attachment.get("size"),
                                "inline": attachment.get("inline"),
                            }
                        )
                    comments.append({
                        'id': comment.get('id'),
                        'author_id': comment.get('author_id'),
                        'body': comment.get('body'),
                        'html_body': comment.get('html_body'),
                        'public': comment.get('public'),
                        'created_at': comment.get('created_at'),
                        'attachments': attachments,
                    })
                url = data.get("next_page")

            logger.info(f"Fetched {len(comments)} Zendesk comments for ticket {ticket_id}")
            return comments
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            logger.error(f"Failed to fetch comments for ticket {ticket_id}: HTTP {e.code} - {e.reason}. {error_body}")
            raise Exception(f"Failed to get comments for ticket {ticket_id}: HTTP {e.code} - {e.reason}. {error_body}")
        except urllib.error.URLError as e:
            logger.error(f"Failed to fetch comments for ticket {ticket_id}: {e}")
            raise Exception(f"Failed to get comments for ticket {ticket_id}: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to fetch comments for ticket {ticket_id}: {e}")
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

#### New Version to strip micro seconds from the timestamp
    def _zendesk_ts(self, dt: "datetime") -> str:
        """
        Format datetime for Zendesk search:
        - no microseconds
        - includes timezone offset (+00:00 etc.)
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.replace(microsecond=0)
        return dt.isoformat()  # e.g. 2026-02-27T22:32:42+00:00

    def _parse_zendesk_datetime(self, value: Optional[str]) -> Optional[dtlib.datetime]:
        if not value:
            return None

        try:
            return dtlib.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Could not parse Zendesk timestamp: %s", value)
            return None

    def _build_ticket_list_item(self, ticket: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        updated_at = ticket.get("updated_at")
        updated_dt = self._parse_zendesk_datetime(updated_at)
        ticket_id = ticket.get("id")
        ticket_url = f"{self.agent_ticket_base_url}/{ticket_id}" if ticket_id is not None else None
        stale_age_hours = None
        stale_age_days = None

        if updated_dt is not None:
            age_seconds = max((now - updated_dt).total_seconds(), 0)
            stale_age_hours = int(age_seconds // 3600)
            stale_age_days = int(age_seconds // 86400)

        return {
            "id": ticket_id,
            "ticket_url": ticket_url,
            "ticket_link": f"[{ticket_id}]({ticket_url})" if ticket_url is not None else None,
            "subject": ticket.get("subject"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "created_at": ticket.get("created_at"),
            "updated_at": updated_at,
            "stale_age_hours": stale_age_hours,
            "stale_age_days": stale_age_days,
        }


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
        created_last_hours: Optional[int] = None,
        stale_hours: Optional[int] = None, # NEW
        include_solved: bool = False, # NEW
        exclude_internal: bool = False,
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
            now = datetime.now(timezone.utc)
            return self.tickets_repository.get_tickets(
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
                now=now,
            )

        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to get tickets: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to get tickets: {str(e)}")

    def search_solved_tickets_for_agent(
        self,
        agent: str,
        solved_after: str,
        solved_before: str,
        max_results: int = 250,
        per_page: int = 100,
        exclude_api_created: bool = False,
    ) -> Dict[str, Any]:
        """
        Search solved tickets for a specific agent in a solved date window.

        Uses the Search API with offset pagination and returns a lightweight ticket list.
        """
        try:
            if not agent or not str(agent).strip():
                raise ValueError("agent is required")
            return self.tickets_repository.search_solved_tickets_for_agent(
                agent=agent,
                solved_after=solved_after,
                solved_before=solved_before,
                max_results=max_results,
                per_page=per_page,
                exclude_api_created=exclude_api_created,
            )
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(
                f"Failed to search solved tickets for agent {agent}: HTTP {e.code} - {e.reason}. {error_body}"
            )
        except Exception as e:
            raise Exception(f"Failed to search solved tickets for agent {agent}: {str(e)}")

    def search_tickets_by_text(
        self,
        phrase: str,
        page: int = 1,
        per_page: int = 25,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        organization: Optional[str] = None,
        updated_since: Optional[str] = None,
        updated_before: Optional[str] = None,
        status: Optional[str] = None,
        include_solved: bool = False,
        exclude_internal: bool = False,
        comment_author: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search tickets by free-text phrase across Zendesk indexed ticket content.

        Supports optional organization, timeframe, status, and comment-author narrowing.
        """
        try:
            phrase_str = str(phrase).strip()
            if not phrase_str:
                raise ValueError("phrase is required")
            now = datetime.now(timezone.utc)
            return self.tickets_repository.search_tickets_by_text(
                phrase=phrase_str,
                page=page,
                per_page=per_page,
                sort_by=sort_by,
                sort_order=sort_order,
                organization=organization,
                updated_since=updated_since,
                updated_before=updated_before,
                status=status,
                include_solved=include_solved,
                exclude_internal=exclude_internal,
                comment_author=comment_author,
                now=now,
            )
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to search tickets by text: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to search tickets by text: {str(e)}")

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
