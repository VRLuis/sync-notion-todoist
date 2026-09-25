"""Access to tasks, projects and the user time zone through the Todoist API v1."""

from __future__ import annotations

import logging
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .http import ApiClient

API_URL = "https://api.todoist.com/api/v1"
TASK_URL = "https://app.todoist.com/app/task/{id}"
PAGE_SIZE = 200  # maximum allowed by the API
INBOX_NAME = "Inbox"

log = logging.getLogger(__name__)


def task_url(task_id: str) -> str:
    # API v1 no longer returns `url` on tasks; this is the documented format.
    return TASK_URL.format(id=task_id)


class TodoistClient:
    def __init__(self, token: str) -> None:
        self.api = ApiClient(API_URL, {"Authorization": f"Bearer {token}"}, max_per_second=5)

    def _paginate(self, path: str, **filters: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        params: dict[str, Any] = {"limit": PAGE_SIZE, **filters}
        while True:
            page = self.api.request("GET", path, params=params)
            items.extend(page["results"])
            if not page.get("next_cursor"):
                return items
            params["cursor"] = page["next_cursor"]

    def get_active_tasks(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return self._paginate("/tasks", **({"project_id": project_id} if project_id else {}))

    def get_project_names(self) -> dict[str, str]:
        # The inbox is named after the account language; the Notion views filter
        # it by a fixed name.
        return {
            p["id"]: INBOX_NAME if p.get("inbox_project") else p["name"]
            for p in self._paginate("/projects")
        }

    def create_project(self, name: str) -> dict[str, Any]:
        return self.api.request("POST", "/projects", idempotent=False, json={"name": name})

    def delete_project(self, project_id: str) -> None:
        # Todoist also deletes the project's tasks; callers only delete empty projects.
        self.api.request("DELETE", f"/projects/{project_id}")

    def create_task(self, fields: dict[str, Any]) -> dict[str, Any]:
        # No retries on 5xx: Todoist does not deduplicate and could create it twice.
        return self.api.request("POST", "/tasks", idempotent=False, json=fields)

    def delete_task(self, task_id: str) -> None:
        self.api.request("DELETE", f"/tasks/{task_id}")

    def update_task(self, task_id: str, fields: dict[str, Any]) -> None:
        self.api.request("POST", f"/tasks/{task_id}", json=fields)

    def move_task(self, task_id: str, project_id: str) -> None:
        self.api.request("POST", f"/tasks/{task_id}/move", json={"project_id": project_id})

    def close_task(self, task_id: str) -> None:
        # A recurring task is not closed: it moves on to its next date.
        self.api.request("POST", f"/tasks/{task_id}/close")

    def reopen_task(self, task_id: str) -> None:
        self.api.request("POST", f"/tasks/{task_id}/reopen")

    def get_user_timezone(self) -> ZoneInfo | None:
        # Needed to place "floating" due times, which have no time zone of their own.
        user = self.api.request("GET", "/user")
        name = (user.get("tz_info") or {}).get("timezone")
        try:
            return ZoneInfo(name) if name else None
        except ZoneInfoNotFoundError:
            log.warning("Unknown Todoist time zone: %s", name)
            return None
