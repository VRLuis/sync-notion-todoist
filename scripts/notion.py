"""Access to the Notion database (data source model, API 2025-09-03+)."""

from __future__ import annotations

import logging
from typing import Any

from .http import ApiClient

API_URL = "https://api.notion.com/v1"
API_VERSION = "2026-03-11"
PAGE_SIZE = 100

log = logging.getLogger(__name__)


class NotionClient:
    def __init__(self, token: str, database_id: str) -> None:
        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": API_VERSION,
            "Content-Type": "application/json",
        }
        # Notion allows an average of 3 requests per second per integration.
        self.api = ApiClient(API_URL, headers, max_per_second=3)
        self.database_id = database_id
        self._data_source_id: str | None = None

    @property
    def data_source_id(self) -> str:
        # Properties and queries now belong to the data source, not to the database.
        if self._data_source_id is None:
            database = self.api.request("GET", f"/databases/{self.database_id}")
            sources = database.get("data_sources") or []
            if not sources:
                raise RuntimeError(f"Database {self.database_id} has no data source")
            if len(sources) > 1:
                log.warning("The database has %d data sources; using the first one", len(sources))
            self._data_source_id = sources[0]["id"]
        return self._data_source_id

    def get_schema(self) -> dict[str, Any]:
        return self.api.request("GET", f"/data_sources/{self.data_source_id}")["properties"]

    def update_schema(self, properties: dict[str, Any]) -> None:
        self.api.request(
            "PATCH", f"/data_sources/{self.data_source_id}", json={"properties": properties}
        )

    def get_view_ids_by_name(self) -> dict[str, str]:
        # The listing only returns IDs; each view has to be fetched for its name.
        listing = self.api.request("GET", "/views", params={"database_id": self.database_id})
        views = (self.api.request("GET", f"/views/{v['id']}") for v in listing["results"])
        return {view["name"]: view["id"] for view in views}

    def create_view(self, view: dict[str, Any], after_view_id: str | None) -> str:
        position = (
            {"type": "after_view", "view_id": after_view_id} if after_view_id else {"type": "start"}
        )
        created = self.api.request(
            "POST",
            "/views",
            idempotent=False,
            json={
                "database_id": self.database_id,
                "data_source_id": self.data_source_id,
                "position": position,
                **view,
            },
        )
        return created["id"]

    def query_all_pages(self) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        body: dict[str, Any] = {"page_size": PAGE_SIZE}
        while True:
            result = self.api.request(
                "POST", f"/data_sources/{self.data_source_id}/query", json=body
            )
            pages.extend(r for r in result["results"] if r.get("object") == "page")
            if not result.get("has_more"):
                return pages
            body["start_cursor"] = result["next_cursor"]

    def create_page(self, properties: dict[str, Any]) -> None:
        self.api.request(
            "POST",
            "/pages",
            idempotent=False,
            json={
                "parent": {"type": "data_source_id", "data_source_id": self.data_source_id},
                "properties": properties,
            },
        )

    def update_page(self, page_id: str, properties: dict[str, Any]) -> None:
        self.api.request("PATCH", f"/pages/{page_id}", json={"properties": properties})
