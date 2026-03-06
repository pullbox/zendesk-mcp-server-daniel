from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from zendesk_mcp_server.infrastructure.zendesk.comments_repository import CommentsRepository
from zendesk_mcp_server.infrastructure.zendesk.comments_write_repository import CommentsWriteRepository
from zendesk_mcp_server.infrastructure.zendesk.field_value_mapper import FieldValueMapper
from zendesk_mcp_server.infrastructure.zendesk.fields_repository import FieldsRepository
from zendesk_mcp_server.infrastructure.zendesk.knowledge_base_repository import KnowledgeBaseRepository
from zendesk_mcp_server.infrastructure.zendesk.ticket_mapper import (
    build_ticket_list_item,
    format_zendesk_timestamp,
)
from zendesk_mcp_server.infrastructure.zendesk.tickets_crud_repository import TicketsCrudRepository
from zendesk_mcp_server.infrastructure.zendesk.tickets_repository import TicketsRepository
from zendesk_mcp_server.infrastructure.zendesk.users_repository import UsersRepository


@dataclass
class ZendeskServices:
    tickets_repository: TicketsRepository
    fields_repository: FieldsRepository
    comments_repository: CommentsRepository
    tickets_crud_repository: TicketsCrudRepository
    comments_write_repository: CommentsWriteRepository
    knowledge_base_repository: KnowledgeBaseRepository
    field_value_mapper: FieldValueMapper
    users_repository: UsersRepository


def build_zendesk_services(
    *,
    base_url: str,
    agent_ticket_base_url: str,
    zenpy_client: Any,
    ticket_factory: Callable[..., Any],
    json_get: Callable[[str], Dict[str, Any]],
    get_ticket_fields: Callable[[], list[Dict[str, Any]]],
    resolve_custom_fields: Callable[[list], Dict[str, Any]],
) -> ZendeskServices:
    fields_repository = FieldsRepository(
        base_url=base_url,
        json_get=json_get,
    )
    field_value_mapper = FieldValueMapper(get_ticket_fields=get_ticket_fields)
    comments_repository = CommentsRepository(
        base_url=base_url,
        json_get=json_get,
    )
    users_repository = UsersRepository(
        base_url=base_url,
        json_get=json_get,
    )
    tickets_repository = TicketsRepository(
        base_url=base_url,
        json_get=json_get,
        build_ticket_list_item=lambda ticket, now: build_ticket_list_item(ticket, now, agent_ticket_base_url),
        timestamp_formatter=format_zendesk_timestamp,
    )
    tickets_crud_repository = TicketsCrudRepository(
        base_url=base_url,
        json_get=json_get,
        resolve_custom_fields=resolve_custom_fields,
        zenpy_client=zenpy_client,
        ticket_factory=ticket_factory,
    )
    comments_write_repository = CommentsWriteRepository(zenpy_client=zenpy_client)
    knowledge_base_repository = KnowledgeBaseRepository(zenpy_client=zenpy_client)

    return ZendeskServices(
        tickets_repository=tickets_repository,
        fields_repository=fields_repository,
        comments_repository=comments_repository,
        tickets_crud_repository=tickets_crud_repository,
        comments_write_repository=comments_write_repository,
        knowledge_base_repository=knowledge_base_repository,
        field_value_mapper=field_value_mapper,
        users_repository=users_repository,
    )
