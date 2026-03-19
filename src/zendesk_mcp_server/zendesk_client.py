from __future__ import annotations

import base64
import json
import logging
import urllib.request
from pathlib import Path
from typing import Any, Dict

from datetime import datetime, timezone
from zenpy import Zenpy
from zenpy.lib.api_objects import Ticket as ZenpyTicket

from zendesk_mcp_server.infrastructure.zendesk.service_container import build_zendesk_services
from zendesk_mcp_server.zendesk_client_mixins import (
    ZendeskFieldsMixin,
    ZendeskReadMixin,
    ZendeskSearchMixin,
    ZendeskWriteMixin,
)

logger = logging.getLogger("zendesk-mcp-client")
_log_handlers = [logging.StreamHandler()]

try:
    _log_handlers.append(logging.FileHandler("zendesk-mcp.log"))
except OSError:
    logger.warning("File logging is unavailable; continuing with stdout logging only.")

logging.basicConfig(
    level=logging.INFO,
    handlers=_log_handlers,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


class ZendeskClient(
    ZendeskFieldsMixin,
    ZendeskReadMixin,
    ZendeskSearchMixin,
    ZendeskWriteMixin,
):
    def __init__(self, subdomain: str, email: str, token: str):
        """
        Initialize the Zendesk client using zenpy lib and direct API.
        """
        self.client = Zenpy(
            subdomain=subdomain,
            email=email,
            token=token,
        )

        self.subdomain = subdomain
        self.email = email
        self.token = token
        self.base_url = f"https://{subdomain}.zendesk.com/api/v2"
        self.agent_ticket_base_url = f"https://{subdomain}.zendesk.com/agent/tickets"

        credentials = f"{email}/token:{token}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode("ascii")
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
        self.users_repository = services.users_repository

    def _json_get(self, url: str, timeout: int = 30) -> Dict[str, Any]:
        req = urllib.request.Request(url)
        req.add_header("Authorization", self.auth_header)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode())

    def _download_to_file(self, url: str, destination: str | Path, timeout: int = 60) -> Path:
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        req = urllib.request.Request(url)
        req.add_header("Authorization", self.auth_header)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            destination_path.write_bytes(response.read())

        return destination_path

    def _current_utc_now(self) -> datetime:
        return datetime.now(timezone.utc)
