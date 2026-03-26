from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

COMMON_TEXT_SEARCH_TERMS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "he",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "there",
    "this",
    "to",
    "was",
    "were",
    "will",
    "with",
}


def build_get_tickets_search_query(
    *,
    agent: Optional[str],
    organization: Optional[str],
    updated_since: Optional[str],
    last_hours: Optional[int],
    created_last_hours: Optional[int],
    stale_hours: Optional[int],
    include_solved: bool,
    exclude_internal: bool,
    now: datetime,
    timestamp_formatter,
) -> tuple[str, Optional[str]]:
    query_parts = ["type:ticket"]

    if agent:
        agent_str = str(agent).strip()
        if agent_str.isdigit():
            query_parts.append(f"assignee_id:{agent_str}")
        else:
            query_parts.append(f'assignee:"{agent_str}"')

    if organization:
        org_str = str(organization).strip()
        query_parts.append(f'organization:"{org_str}"')

    computed_updated_since = updated_since
    updated_before = None

    if stale_hours is not None:
        stale_dt = now - timedelta(hours=int(stale_hours))
        updated_before = timestamp_formatter(stale_dt)

    if stale_hours is not None and not include_solved:
        query_parts.append("status<solved")

    if exclude_internal:
        query_parts.append("-tags:internal")

    if last_hours is not None:
        since_dt = now - timedelta(hours=int(last_hours))
        computed_updated_since = timestamp_formatter(since_dt)

    if created_last_hours is not None:
        created_dt = now - timedelta(hours=int(created_last_hours))
        query_parts.append(f"created>{timestamp_formatter(created_dt)}")

    if updated_before:
        query_parts.append(f"updated<{updated_before}")

    if computed_updated_since:
        since_str = str(computed_updated_since).strip()
        query_parts.append(f"updated>{since_str}")

    return " ".join(query_parts), computed_updated_since


def build_solved_tickets_query(agent: str, solved_after: str, solved_before: str) -> str:
    agent_str = str(agent).strip()
    query_parts = ["type:ticket", f"updated>={solved_after}", f"updated<{solved_before}"]
    if agent_str.isdigit():
        query_parts.append(f"assignee_id:{agent_str}")
    else:
        query_parts.append(f'assignee:"{agent_str}"')
    return " ".join(query_parts)


def build_text_search_query(
    *,
    phrase: str,
    organization: Optional[str],
    updated_since: Optional[str],
    updated_before: Optional[str],
    status: Optional[str],
    include_solved: bool,
    exclude_internal: bool,
    comment_author: Optional[str],
) -> str:
    phrase_str = str(phrase).strip()
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

    return " ".join(query_parts)


def build_partial_text_search_query(
    *,
    phrase: str,
    organization: Optional[str],
    updated_since: Optional[str],
    updated_before: Optional[str],
    status: Optional[str],
    include_solved: bool,
    exclude_internal: bool,
    comment_author: Optional[str],
) -> tuple[str | None, str | None]:
    phrase_str = str(phrase).strip()
    if not phrase_str:
        return None, "phrase is required"

    normalized_tokens: list[str] = []
    for raw_token in phrase_str.replace('"', " ").split():
        token = raw_token.strip().strip(".,:;()[]{}<>!?")
        if not token:
            continue
        normalized_tokens.append(token)

    if not normalized_tokens:
        return None, "phrase does not contain searchable tokens"

    significant_tokens = [
        token
        for token in normalized_tokens
        if len(token) >= 4 and token.lower() not in COMMON_TEXT_SEARCH_TERMS
    ]
    if not significant_tokens:
        return None, "phrase is too short or too common for safe partial fallback"

    query_parts = ["type:ticket", *significant_tokens]

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

    return " ".join(query_parts), None


def build_tag_scan_query(
    *,
    tag: str,
    exclude_internal: bool,
) -> str:
    tag_str = str(tag).strip()
    if not tag_str:
        raise ValueError("tag is required")

    query_parts = ["type:ticket", f"tags:{tag_str}", "status:open"]

    if exclude_internal:
        query_parts.append("-tags:internal")

    return " ".join(query_parts)
