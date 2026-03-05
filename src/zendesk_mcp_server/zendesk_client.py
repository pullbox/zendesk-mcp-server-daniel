import datetime as dtlib
from typing import Dict, Any, List, Optional
import json
import urllib.error
import urllib.request
import urllib.parse
import base64
import logging

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
        stale_age_hours = None
        stale_age_days = None

        if updated_dt is not None:
            age_seconds = max((now - updated_dt).total_seconds(), 0)
            stale_age_hours = int(age_seconds // 3600)
            stale_age_days = int(age_seconds // 86400)

        return {
            "id": ticket.get("id"),
            "subject": ticket.get("subject"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "created_at": ticket.get("created_at"),
            "updated_at": updated_at,
            "stale_age_hours": stale_age_hours,
            "stale_age_days": stale_age_days,
        }


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
            per_page = min(per_page, 100)
            now = datetime.now(timezone.utc)

            # If any filters are present, use the Search API
            # NEW UPDATED
            if (
                agent
                or organization
                or updated_since
                or last_hours is not None
                or created_last_hours is not None
                or stale_hours is not None
                or exclude_internal
            ):
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
                    updated_before = self._zendesk_ts(dt)

                # If using stale detection, default to open-ish tickets unless caller requests otherwise
                # This keeps out solved/closed tickets
                if stale_hours is not None and not include_solved:
                    query_parts.append("status<solved")

                if exclude_internal:
                    query_parts.append("-tags:internal")

                # Apply updated constraints
                if last_hours is not None:
                    dt = datetime.now(timezone.utc) - timedelta(hours=int(last_hours))
                    updated_since = self._zendesk_ts(dt)

                if created_last_hours is not None:
                    dt = datetime.now(timezone.utc) - timedelta(hours=int(created_last_hours))
                    query_parts.append(f"created>{self._zendesk_ts(dt)}")

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
                logger.info(f"Fetching Zendesk tickets from search API: {url}")
                logger.info(f"Zendesk search query: {query if 'query' in locals() else 'tickets.json'}")
                data = self._json_get(url)
                logger.info(f"Fetched {len(data.get('results', []))} raw search results from Zendesk")

                results = data.get("results", [])

                ticket_list = []
                for item in results:
                    # Guard: only tickets
                    if item.get("result_type") not in (None, "ticket"):
                        continue

                    ticket_list.append(self._build_ticket_list_item(item, now))

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
                        "created_last_hours": created_last_hours,
                        "stale_hours": stale_hours,
                        "include_solved": include_solved,
                        "exclude_internal": exclude_internal,
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
            logger.info(f"Fetching Zendesk tickets from list API: {url}")
            data = self._json_get(url)
            logger.info(f"Fetched {len(data.get('tickets', []))} raw tickets from Zendesk")

            tickets_data = data.get("tickets", [])

            ticket_list = []
            for ticket in tickets_data:
                ticket_list.append(self._build_ticket_list_item(ticket, now))

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

            per_page = max(1, min(per_page, 100))
            max_results = max(1, min(max_results, 1000))

            agent_str = str(agent).strip()
            query_parts = ["type:ticket", "status:solved", f"solved>={solved_after}", f"solved<{solved_before}"]
            if agent_str.isdigit():
                query_parts.append(f"assignee_id:{agent_str}")
            else:
                query_parts.append(f'assignee:"{agent_str}"')

            query = " ".join(query_parts)
            params = {
                "query": query,
                "page": "1",
                "per_page": str(per_page),
                "sort_by": "created_at",
                "sort_order": "desc",
            }
            url = f"{self.base_url}/search.json?{urllib.parse.urlencode(params)}"

            collected: List[Dict[str, Any]] = []
            total_matches: int | None = None
            page = 1
            excluded_api_created_count = 0

            while url and len(collected) < max_results:
                logger.info(f"Fetching solved-ticket search page {page}: {url}")
                data = self._json_get(url)
                if total_matches is None and isinstance(data.get("count"), int):
                    total_matches = data["count"]

                results = data.get("results", [])
                for item in results:
                    if item.get("result_type") not in (None, "ticket"):
                        continue
                    via_channel = ((item.get("via") or {}).get("channel"))
                    if exclude_api_created and via_channel == "api":
                        excluded_api_created_count += 1
                        continue
                    collected.append({
                        "id": item.get("id"),
                        "subject": item.get("subject"),
                        "status": item.get("status"),
                        "priority": item.get("priority"),
                        "created_at": item.get("created_at"),
                        "updated_at": item.get("updated_at"),
                    })
                    if len(collected) >= max_results:
                        break

                url = data.get("next_page")
                page += 1

            retrieved_count = len(collected)
            return {
                "tickets": collected,
                "query": query,
                "total_matches": total_matches if total_matches is not None else retrieved_count,
                "retrieved_count": retrieved_count,
                "truncated": bool(url),
                "excluded_api_created_count": excluded_api_created_count,
            }
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

            per_page = max(1, min(per_page, 100))
            now = datetime.now(timezone.utc)

            escaped_phrase = phrase_str.replace('"', '\\"')
            query_parts = ["type:ticket", f'"{escaped_phrase}"']

            if organization:
                org_str = str(organization).strip()
                if org_str:
                    query_parts.append(f'organization:"{org_str}"')

            if updated_since:
                query_parts.append(f"updated>{str(updated_since).strip()}")

            if updated_before:
                query_parts.append(f"updated<{str(updated_before).strip()}")

            if status:
                query_parts.append(f"status:{str(status).strip()}")
            elif not include_solved:
                query_parts.append("status<solved")

            if exclude_internal:
                query_parts.append("-tags:internal")

            if comment_author:
                commenter = str(comment_author).strip()
                if commenter.isdigit():
                    query_parts.append(f"commenter:{commenter}")
                elif commenter:
                    query_parts.append(f'commenter:"{commenter}"')

            query = " ".join(query_parts)
            params = {
                "query": query,
                "page": str(page),
                "per_page": str(per_page),
                "sort_by": sort_by,
                "sort_order": sort_order,
            }
            url = f"{self.base_url}/search.json?{urllib.parse.urlencode(params)}"

            logger.info(f"Searching tickets by text via search API: {url}")
            data = self._json_get(url)

            results = data.get("results", [])
            ticket_list = []
            for item in results:
                if item.get("result_type") not in (None, "ticket"):
                    continue
                ticket_list.append(self._build_ticket_list_item(item, now))

            return {
                "tickets": ticket_list,
                "page": page,
                "per_page": per_page,
                "count": len(ticket_list),
                "sort_by": sort_by,
                "sort_order": sort_order,
                "query": query,
                "filters": {
                    "phrase": phrase_str,
                    "organization": organization,
                    "updated_since": updated_since,
                    "updated_before": updated_before,
                    "status": status,
                    "include_solved": include_solved,
                    "exclude_internal": exclude_internal,
                    "comment_author": comment_author,
                },
                "has_more": data.get("next_page") is not None,
                "next_page": page + 1 if data.get("next_page") else None,
                "previous_page": page - 1 if page > 1 else None,
            }
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
