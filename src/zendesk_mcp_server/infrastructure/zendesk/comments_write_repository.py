from __future__ import annotations

from zenpy.lib.api_objects import Comment


class CommentsWriteRepository:
    def __init__(self, *, zenpy_client) -> None:
        self._zenpy_client = zenpy_client

    def post_comment(self, ticket_id: int, comment: str, public: bool = True) -> str:
        ticket = self._zenpy_client.tickets(id=ticket_id)
        ticket.comment = Comment(
            html_body=comment,
            public=public,
        )
        self._zenpy_client.tickets.update(ticket)
        return comment
