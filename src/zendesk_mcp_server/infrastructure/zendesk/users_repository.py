from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Callable, Dict, List

logger = logging.getLogger("zendesk-mcp-client")


class UsersRepository:
    def __init__(self, *, base_url: str, json_get: Callable[[str], Dict[str, Any]]) -> None:
        self.base_url = base_url
        self._json_get = json_get

    def get_user(self, user_id: int) -> Dict[str, Any]:
        logger.info("Fetching Zendesk user %s", user_id)
        data = self._json_get(f"{self.base_url}/users/{user_id}.json")
        return self._normalize_user(data.get("user") or {})

    def search_users(self, query: str, page: int = 1, per_page: int = 25) -> Dict[str, Any]:
        query_value = str(query).strip()
        if not query_value:
            raise ValueError("query is required")

        params = {
            "query": query_value,
            "page": str(max(1, int(page))),
            "per_page": str(max(1, min(int(per_page), 100))),
        }
        url = f"{self.base_url}/users/search.json?{urllib.parse.urlencode(params)}"
        data = self._json_get(url)
        users = [self._normalize_user(user) for user in data.get("users", [])]
        return {
            "users": users,
            "count": int(data.get("count", len(users))),
            "next_page": data.get("next_page"),
            "previous_page": data.get("previous_page"),
            "query": query_value,
            "page": int(params["page"]),
            "per_page": int(params["per_page"]),
        }

    def get_users_by_ids(self, user_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        ids = sorted({int(user_id) for user_id in user_ids if user_id is not None})
        if not ids:
            return {}

        # Zendesk show_many accepts comma-separated IDs; keep chunks small and predictable.
        chunk_size = 100
        result: Dict[int, Dict[str, Any]] = {}
        for start in range(0, len(ids), chunk_size):
            chunk = ids[start : start + chunk_size]
            params = {"ids": ",".join(str(user_id) for user_id in chunk)}
            url = f"{self.base_url}/users/show_many.json?{urllib.parse.urlencode(params)}"
            data = self._json_get(url)
            for raw_user in data.get("users", []):
                user = self._normalize_user(raw_user)
                if user.get("id") is not None:
                    result[int(user["id"])] = user
        return result

    @staticmethod
    def _normalize_user(user: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": user.get("id"),
            "name": user.get("name"),
            "email": user.get("email"),
            "active": user.get("active"),
            "role": user.get("role"),
            "organization_id": user.get("organization_id"),
            "external_id": user.get("external_id"),
        }
