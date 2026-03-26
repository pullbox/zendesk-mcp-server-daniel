from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from zendesk_mcp_server.infrastructure.zendesk.query_builder import (
    build_partial_text_search_query,
    build_get_tickets_search_query,
    build_tag_scan_query,
    build_solved_tickets_query,
    build_text_search_query,
)

logger = logging.getLogger("zendesk-mcp-client")


class TicketsRepository:
    def __init__(
        self,
        *,
        base_url: str,
        json_get: Callable[[str], Dict[str, Any]],
        build_ticket_list_item: Callable[[Dict[str, Any], datetime], Dict[str, Any]],
        timestamp_formatter: Callable[[datetime], str],
    ) -> None:
        self.base_url = base_url
        self._json_get = json_get
        self._build_ticket_list_item = build_ticket_list_item
        self._timestamp_formatter = timestamp_formatter

    def _search_ticket_items(
        self,
        *,
        query: str,
        page: int,
        per_page: int,
        sort_by: str,
        sort_order: str,
        now: datetime,
        match_type: str,
    ) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
        params = {
            "query": query,
            "page": str(page),
            "per_page": str(per_page),
            "sort_by": sort_by,
            "sort_order": sort_order,
        }
        url = f"{self.base_url}/search.json?{urllib.parse.urlencode(params)}"

        logger.info("Searching tickets by text via search API: %s", url)
        data = self._json_get(url)

        ticket_list: list[Dict[str, Any]] = []
        for item in data.get("results", []):
            if item.get("result_type") not in (None, "ticket"):
                continue
            ticket_item = self._build_ticket_list_item(item, now)
            ticket_item["match_type"] = match_type
            ticket_list.append(ticket_item)

        return ticket_list, data

    def get_tickets(
        self,
        *,
        page: int,
        per_page: int,
        sort_by: str,
        sort_order: str,
        agent: Optional[str],
        organization: Optional[str],
        updated_since: Optional[str],
        last_hours: Optional[int],
        created_last_hours: Optional[int],
        stale_hours: Optional[int],
        include_solved: bool,
        exclude_internal: bool,
        now: datetime,
    ) -> Dict[str, Any]:
        per_page = min(per_page, 100)

        use_search = bool(
            agent
            or organization
            or updated_since
            or last_hours is not None
            or created_last_hours is not None
            or stale_hours is not None
            or exclude_internal
        )

        if use_search:
            query, computed_updated_since = build_get_tickets_search_query(
                agent=agent,
                organization=organization,
                updated_since=updated_since,
                last_hours=last_hours,
                created_last_hours=created_last_hours,
                stale_hours=stale_hours,
                include_solved=include_solved,
                exclude_internal=exclude_internal,
                now=now,
                timestamp_formatter=self._timestamp_formatter,
            )
            params = {
                "query": query,
                "page": str(page),
                "per_page": str(per_page),
                "sort_by": sort_by,
                "sort_order": sort_order,
            }
            url = f"{self.base_url}/search.json?{urllib.parse.urlencode(params)}"

            logger.info("Fetching Zendesk tickets from search API: %s", url)
            logger.info("Zendesk search query: %s", query)
            data = self._json_get(url)
            logger.info("Fetched %s raw search results from Zendesk", len(data.get("results", [])))

            ticket_list = [
                self._build_ticket_list_item(item, now)
                for item in data.get("results", [])
                if item.get("result_type") in (None, "ticket")
            ]

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
                    "updated_since": computed_updated_since,
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

        params = {
            "page": str(page),
            "per_page": str(per_page),
            "sort_by": sort_by,
            "sort_order": sort_order,
        }
        url = f"{self.base_url}/tickets.json?{urllib.parse.urlencode(params)}"
        logger.info("Fetching Zendesk tickets from list API: %s", url)
        data = self._json_get(url)
        logger.info("Fetched %s raw tickets from Zendesk", len(data.get("tickets", [])))

        ticket_list = [self._build_ticket_list_item(ticket, now) for ticket in data.get("tickets", [])]

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

    def search_solved_tickets_for_agent(
        self,
        *,
        agent: str,
        solved_after: str,
        solved_before: str,
        max_results: int,
        per_page: int,
        exclude_api_created: bool,
    ) -> Dict[str, Any]:
        per_page = max(1, min(per_page, 100))
        max_results = max(1, min(max_results, 1000))

        query = build_solved_tickets_query(agent, solved_after, solved_before)
        params = {
            "query": query,
            "page": "1",
            "per_page": str(per_page),
            "sort_by": "created_at",
            "sort_order": "desc",
        }
        url = f"{self.base_url}/search.json?{urllib.parse.urlencode(params)}"

        collected: list[Dict[str, Any]] = []
        total_matches: int | None = None
        page = 1
        excluded_api_created_count = 0

        while url and len(collected) < max_results:
            logger.info("Fetching resolved-ticket search page %s: %s", page, url)
            data = self._json_get(url)
            if total_matches is None and isinstance(data.get("count"), int):
                total_matches = data["count"]

            for item in data.get("results", []):
                if item.get("result_type") not in (None, "ticket"):
                    continue
                status = str(item.get("status") or "").lower()
                if status not in {"solved", "closed"}:
                    continue
                via_channel = ((item.get("via") or {}).get("channel"))
                if exclude_api_created and via_channel == "api":
                    excluded_api_created_count += 1
                    continue
                collected.append(
                    {
                        "id": item.get("id"),
                        "subject": item.get("subject"),
                        "status": item.get("status"),
                        "priority": item.get("priority"),
                        "created_at": item.get("created_at"),
                        "updated_at": item.get("updated_at"),
                    }
                )
                if len(collected) >= max_results:
                    break

            url = data.get("next_page")
            page += 1

        retrieved_count = len(collected)
        truncated = bool(url)
        effective_total_matches = retrieved_count if not truncated else (total_matches if total_matches is not None else retrieved_count)
        return {
            "tickets": collected,
            "query": query,
            "total_matches": effective_total_matches,
            "retrieved_count": retrieved_count,
            "truncated": truncated,
            "excluded_api_created_count": excluded_api_created_count,
        }

    def search_tickets_by_text(
        self,
        *,
        phrase: str,
        page: int,
        per_page: int,
        sort_by: str,
        sort_order: str,
        organization: Optional[str],
        updated_since: Optional[str],
        updated_before: Optional[str],
        status: Optional[str],
        include_solved: bool,
        exclude_internal: bool,
        comment_author: Optional[str],
        now: datetime,
    ) -> Dict[str, Any]:
        per_page = max(1, min(per_page, 100))

        exact_query = build_text_search_query(
            phrase=phrase,
            organization=organization,
            updated_since=updated_since,
            updated_before=updated_before,
            status=status,
            include_solved=include_solved,
            exclude_internal=exclude_internal,
            comment_author=comment_author,
        )
        ticket_list, data = self._search_ticket_items(
            query=exact_query,
            page=page,
            per_page=per_page,
            sort_by=sort_by,
            sort_order=sort_order,
            now=now,
            match_type="exact",
        )

        partial_query = None
        partial_fallback_used = False
        partial_fallback_reason = None
        search_mode = "exact"
        exact_count = len(ticket_list)

        if exact_count == 0:
            partial_query, partial_fallback_reason = build_partial_text_search_query(
                phrase=phrase,
                organization=organization,
                updated_since=updated_since,
                updated_before=updated_before,
                status=status,
                include_solved=include_solved,
                exclude_internal=exclude_internal,
                comment_author=comment_author,
            )
            if partial_query:
                partial_ticket_list, partial_data = self._search_ticket_items(
                    query=partial_query,
                    page=page,
                    per_page=per_page,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    now=now,
                    match_type="partial",
                )
                ticket_list = partial_ticket_list
                data = partial_data
                partial_fallback_used = True
                search_mode = "partial_fallback"
            else:
                search_mode = "exact_no_partial_fallback"

        return {
            "tickets": ticket_list,
            "page": page,
            "per_page": per_page,
            "count": len(ticket_list),
            "sort_by": sort_by,
            "sort_order": sort_order,
            "query": partial_query if partial_fallback_used and partial_query else exact_query,
            "exact_query": exact_query,
            "partial_query": partial_query,
            "search_mode": search_mode,
            "exact_count": exact_count,
            "partial_fallback_used": partial_fallback_used,
            "partial_fallback_reason": partial_fallback_reason,
            "filters": {
                "phrase": str(phrase).strip(),
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

    def search_open_tickets_by_tag(
        self,
        *,
        tag: str,
        max_results: int,
        per_page: int,
        include_solved: bool,
        exclude_internal: bool,
        now: datetime,
    ) -> Dict[str, Any]:
        per_page = max(1, min(per_page, 100))
        max_results = max(1, min(max_results, 1000))

        query = build_tag_scan_query(
            tag=tag,
            exclude_internal=exclude_internal,
        )
        params = {
            "query": query,
            "page": "1",
            "per_page": str(per_page),
            "sort_by": "updated_at",
            "sort_order": "desc",
        }
        url = f"{self.base_url}/search.json?{urllib.parse.urlencode(params)}"

        collected: list[Dict[str, Any]] = []
        total_matches: int | None = None
        page = 1

        while url and len(collected) < max_results:
            logger.info("Fetching tag-scan page %s: %s", page, url)
            data = self._json_get(url)
            if total_matches is None and isinstance(data.get("count"), int):
                total_matches = data["count"]

            for item in data.get("results", []):
                if item.get("result_type") not in (None, "ticket"):
                    continue
                collected.append(self._build_ticket_list_item(item, now))
                if len(collected) >= max_results:
                    break

            url = data.get("next_page")
            page += 1

        retrieved_count = len(collected)
        truncated = bool(url)
        effective_total_matches = retrieved_count if not truncated else (total_matches if total_matches is not None else retrieved_count)
        return {
            "tickets": collected,
            "query": query,
            "total_matches": effective_total_matches,
            "retrieved_count": retrieved_count,
            "truncated": truncated,
        }
