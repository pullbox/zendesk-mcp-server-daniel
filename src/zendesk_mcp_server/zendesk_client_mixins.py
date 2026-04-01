from __future__ import annotations

import logging
import urllib.error
from typing import Any, Dict, List, Optional

logger = logging.getLogger("zendesk-mcp-client")


class ZendeskFieldsMixin:
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


class ZendeskReadMixin:
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

    def get_all_articles(self) -> Dict[str, Any]:
        """
        Fetch help center articles as knowledge base.
        Returns a Dict of section -> [article].
        """
        try:
            return self.knowledge_base_repository.get_all_articles()
        except Exception as e:
            raise Exception(f"Failed to fetch knowledge base: {str(e)}")

    def get_user(self, user_id: int) -> Dict[str, Any]:
        """
        Fetch a Zendesk user by ID.
        """
        try:
            return self.users_repository.get_user(user_id)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            logger.error(f"Failed to fetch Zendesk user {user_id}: HTTP {e.code} - {e.reason}. {error_body}")
            raise Exception(f"Failed to get user {user_id}: HTTP {e.code} - {e.reason}. {error_body}")
        except urllib.error.URLError as e:
            logger.error(f"Failed to fetch Zendesk user {user_id}: {e}")
            raise Exception(f"Failed to get user {user_id}: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to fetch Zendesk user {user_id}: {e}")
            raise Exception(f"Failed to get user {user_id}: {str(e)}")

    def get_users_by_ids(self, user_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """
        Fetch a bulk map of users by ID.
        """
        try:
            return self.users_repository.get_users_by_ids(user_ids)
        except Exception as e:
            logger.error(f"Failed to fetch Zendesk users by ids: {e}")
            raise Exception(f"Failed to get users by ids: {str(e)}")

    def search_users(self, query: str, page: int = 1, per_page: int = 25) -> Dict[str, Any]:
        """
        Search users by name/email.
        """
        try:
            return self.users_repository.search_users(query=query, page=page, per_page=per_page)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            logger.error(f"Failed to search Zendesk users: HTTP {e.code} - {e.reason}. {error_body}")
            raise Exception(f"Failed to search users: HTTP {e.code} - {e.reason}. {error_body}")
        except urllib.error.URLError as e:
            logger.error(f"Failed to search Zendesk users: {e}")
            raise Exception(f"Failed to search users: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to search Zendesk users: {e}")
            raise Exception(f"Failed to search users: {str(e)}")

    def resolve_user(self, identifier: str | int) -> Dict[str, Any] | None:
        """
        Resolve an identifier (id/email/name) to a user profile.
        """
        token = str(identifier).strip()
        if not token:
            return None
        if token.isdigit():
            return self.get_user(int(token))

        result = self.search_users(query=token, page=1, per_page=100)
        users = result.get("users", [])
        if not users:
            return None

        lowered_token = token.casefold()
        exact_email = next((u for u in users if str(u.get("email") or "").casefold() == lowered_token), None)
        if exact_email is not None:
            return exact_email
        exact_name = next((u for u in users if str(u.get("name") or "").casefold() == lowered_token), None)
        if exact_name is not None:
            return exact_name
        return users[0]


class ZendeskSearchMixin:
    def _resolve_filter_user_identifier(self, identifier: Optional[str]) -> Optional[str]:
        if identifier is None:
            return None
        token = str(identifier).strip()
        if not token or token.isdigit():
            return token or None
        if "@" not in token:
            return token
        try:
            user = self.resolve_user(token)
        except Exception:
            return token
        if not user or user.get("id") is None:
            return token
        return str(user["id"])

    def get_tickets(
        self,
        page: int = 1,
        per_page: int = 25,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        agent: Optional[str] = None,
        organization: Optional[str] = None,
        updated_since: Optional[str] = None,
        last_hours: Optional[int] = None,
        created_last_hours: Optional[int] = None,
        stale_hours: Optional[int] = None,
        include_solved: bool = False,
        exclude_internal: bool = False,
    ) -> Dict[str, Any]:
        """
        Get tickets with optional filtering.
        """
        try:
            now = self._current_utc_now()
            resolved_agent = self._resolve_filter_user_identifier(agent)
            return self.tickets_repository.get_tickets(
                page=page,
                per_page=per_page,
                sort_by=sort_by,
                sort_order=sort_order,
                agent=resolved_agent,
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
        resolve_agent_id: bool = False,
    ) -> Dict[str, Any]:
        """
        Search solved tickets for a specific agent in a solved date window.

        Uses the Search API with offset pagination and returns a lightweight ticket list.
        """
        try:
            if not agent or not str(agent).strip():
                raise ValueError("agent is required")
            resolved_agent = self._resolve_filter_user_identifier(agent)
            if resolve_agent_id and not str(agent).strip().isdigit():
                try:
                    resolved_user = self.resolve_user(agent)
                    if resolved_user and resolved_user.get("id") is not None:
                        resolved_agent = str(resolved_user["id"])
                except Exception:
                    # Fall back to existing identifier behavior if user lookup fails.
                    pass
            return self.tickets_repository.search_solved_tickets_for_agent(
                agent=resolved_agent or agent,
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
            now = self._current_utc_now()
            resolved_comment_author = self._resolve_filter_user_identifier(comment_author)
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
                comment_author=resolved_comment_author,
                now=now,
            )
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to search tickets by text: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to search tickets by text: {str(e)}")

    def search_open_tickets_by_tag(
        self,
        *,
        tag: str,
        max_results: int = 250,
        per_page: int = 100,
        include_solved: bool = False,
        exclude_internal: bool = False,
    ) -> Dict[str, Any]:
        """
        Search tickets by tag using the Search API, without a created-date window.
        """
        try:
            now = self._current_utc_now()
            return self.tickets_repository.search_open_tickets_by_tag(
                tag=tag,
                max_results=max_results,
                per_page=per_page,
                include_solved=include_solved,
                exclude_internal=exclude_internal,
                now=now,
            )
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to search tickets by tag {tag}: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to search tickets by tag {tag}: {str(e)}")

    def search_open_tickets_by_query(
        self,
        *,
        query: str,
        max_results: int = 250,
        per_page: int = 100,
    ) -> Dict[str, Any]:
        """
        Search tickets by an arbitrary pre-built Zendesk search query.
        """
        try:
            now = self._current_utc_now()
            return self.tickets_repository.search_open_tickets_by_query(
                query=query,
                max_results=max_results,
                per_page=per_page,
                now=now,
            )
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to search tickets by query: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to search tickets by query: {str(e)}")


class ZendeskWriteMixin:
    def post_comment(self, ticket_id: int, comment: str, public: bool = True) -> str:
        """
        Post a comment to an existing ticket.
        """
        try:
            return self.comments_write_repository.post_comment(ticket_id, comment, public)
        except Exception as e:
            raise Exception(f"Failed to post comment on ticket {ticket_id}: {str(e)}")

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
        """
        try:
            return self.tickets_crud_repository.update_ticket(ticket_id, fields)
        except Exception as e:
            raise Exception(f"Failed to update ticket {ticket_id}: {str(e)}")
