from typing import Dict, Any, List, Optional
import json
import urllib.error
import urllib.request
import base64
import logging

from zendesk_mcp_server.infrastructure.zendesk.service_container import build_zendesk_services

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
        services = build_zendesk_services(
            base_url=self.base_url,
            agent_ticket_base_url=self.agent_ticket_base_url,
            zenpy_client=self.client,
            ticket_factory=ZenpyTicket,
            json_get=lambda url: self._json_get(url),
            get_ticket_fields=lambda: self.get_ticket_fields(),
            resolve_custom_fields=lambda raw: self._resolve_custom_fields(raw),
        )
        self.tickets_repository = services.tickets_repository
        self.fields_repository = services.fields_repository
        self.comments_repository = services.comments_repository
        self.tickets_crud_repository = services.tickets_crud_repository
        self.comments_write_repository = services.comments_write_repository
        self.knowledge_base_repository = services.knowledge_base_repository
        self.field_value_mapper = services.field_value_mapper

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
            return self.fields_repository.get_ticket_fields()
        except Exception as e:
            logger.error(f"Failed to get Zendesk ticket fields: {e}")
            raise Exception(f"Failed to get ticket fields: {str(e)}")

    def get_ticket_field_definitions(self) -> List[Dict[str, Any]]:
        """
        Fetch full ticket field definitions so option metadata can be resolved dynamically.
        """
        try:
            return self.fields_repository.get_ticket_field_definitions()
        except Exception as e:
            logger.error(f"Failed to get Zendesk ticket field definitions: {e}")
            raise Exception(f"Failed to get ticket field definitions: {str(e)}")

    def get_ticket_field_options(self, ticket_field_id: int) -> List[Dict[str, Any]]:
        """
        Fetch selectable options for a specific ticket field.
        """
        try:
            return self.fields_repository.get_ticket_field_options(ticket_field_id)
        except Exception as e:
            logger.error(f"Failed to get Zendesk ticket field options for field {ticket_field_id}: {e}")
            raise Exception(f"Failed to get ticket field options for field {ticket_field_id}: {str(e)}")

    def _resolve_custom_fields(self, raw: list) -> Dict[str, Any]:
        """
        Convert a raw custom_fields list [{id, value}, ...] from the REST API
        into a {field_title: value} dict, omitting null values.
        """
        return self.field_value_mapper.resolve_custom_fields(raw)

    def get_ticket(self, ticket_id: int) -> Dict[str, Any]:
        """
        Query a ticket by its ID, including resolved custom field values.
        """
        try:
            return self.tickets_crud_repository.get_ticket(ticket_id)
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
            return self.comments_repository.get_ticket_comments(ticket_id)
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
            return self.comments_write_repository.post_comment(ticket_id, comment, public)
        except Exception as e:
            raise Exception(f"Failed to post comment on ticket {ticket_id}: {str(e)}")

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
            return self.knowledge_base_repository.get_all_articles()
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
            return self.tickets_crud_repository.create_ticket(
                subject=subject,
                description=description,
                requester_id=requester_id,
                assignee_id=assignee_id,
                priority=priority,
                type=type,
                tags=tags,
                custom_fields=custom_fields,
            )
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
            return self.tickets_crud_repository.update_ticket(ticket_id, fields)
        except Exception as e:
            raise Exception(f"Failed to update ticket {ticket_id}: {str(e)}")
