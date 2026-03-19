from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger("zendesk-mcp-client")


class CommentsRepository:
    def __init__(self, *, base_url: str, json_get: Callable[[str], Dict[str, Any]]) -> None:
        self.base_url = base_url
        self._json_get = json_get

    def get_ticket_comments(self, ticket_id: int) -> List[Dict[str, Any]]:
        logger.info("Fetching Zendesk comments for ticket %s", ticket_id)
        comments: List[Dict[str, Any]] = []
        url = f"{self.base_url}/tickets/{ticket_id}/comments.json"

        while url:
            data = self._json_get(url)
            for comment in data.get("comments", []):
                attachments = [
                    {
                        "id": attachment.get("id"),
                        "file_name": attachment.get("file_name"),
                        "content_type": attachment.get("content_type"),
                        "size": attachment.get("size"),
                        "inline": attachment.get("inline"),
                        "content_url": attachment.get("content_url"),
                        "mapped_content_url": attachment.get("mapped_content_url"),
                    }
                    for attachment in (comment.get("attachments", []) or [])
                ]
                comments.append(
                    {
                        "id": comment.get("id"),
                        "author_id": comment.get("author_id"),
                        "body": comment.get("body"),
                        "html_body": comment.get("html_body"),
                        "public": comment.get("public"),
                        "created_at": comment.get("created_at"),
                        "attachments": attachments,
                    }
                )
            url = data.get("next_page")

        logger.info("Fetched %s Zendesk comments for ticket %s", len(comments), ticket_id)
        return comments
