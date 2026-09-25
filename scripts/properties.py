"""Conversion between Todoist tasks and Notion properties, and comparison between them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .schema import (
    PRIORITY_LABELS,
    PROP_COMPLETED,
    PROP_DATE,
    PROP_DESCRIPTION,
    PROP_LABELS,
    PROP_LINK,
    PROP_NAME,
    PROP_PRIORITY,
    PROP_PROJECT,
    PROP_TODOIST_ID,
)
from .todoist import INBOX_NAME, task_url

TEXT_LIMIT = 2000  # characters per rich text object
RICH_TEXT_ITEMS = 100  # objects per rich text array
OPTION_LIMIT = 100  # characters per select/multi-select option


def rich_text(text: str) -> list[dict[str, Any]]:
    chunks = [text[i : i + TEXT_LIMIT] for i in range(0, len(text), TEXT_LIMIT)]
    return [{"type": "text", "text": {"content": c}} for c in chunks[:RICH_TEXT_ITEMS]]


def plain_text(items: list[dict[str, Any]] | None) -> str:
    # Requests carry the text in text.content; responses carry it in plain_text.
    return "".join(i.get("plain_text", i.get("text", {}).get("content", "")) for i in items or [])


def option_name(name: str) -> str:
    # Notion does not allow commas in select/multi-select option names.
    return name.replace(",", " ").strip()[:OPTION_LIMIT]


def due_to_notion(due: dict[str, Any] | None, user_tz: ZoneInfo | None) -> dict[str, Any] | None:
    """Convert a Todoist `due` into a Notion date value.

    Todoist uses three formats in `due.date`:
      - all day:          "2025-02-10"
      - floating time:    "2025-02-10T12:00:00"          (user's local time)
      - fixed time zone:  "2025-02-10T11:00:00.000000Z"  (UTC)
    """
    if not due or not due.get("date"):
        return None
    value: str = due["date"]
    if "T" in value and not value.endswith("Z") and user_tz:
        return {"start": value, "time_zone": user_tz.key}
    return {"start": value}


def task_to_properties(
    task: dict[str, Any], projects: dict[str, str], user_tz: ZoneInfo | None
) -> dict[str, Any]:
    project = projects.get(task["project_id"])
    priority = PRIORITY_LABELS.get(task.get("priority", 1), "P4")
    labels = sorted({option_name(label) for label in task.get("labels", []) if option_name(label)})
    return {
        PROP_NAME: {"title": rich_text(task.get("content", ""))},
        PROP_DESCRIPTION: {"rich_text": rich_text(task.get("description", ""))},
        PROP_DATE: {"date": due_to_notion(task.get("due"), user_tz)},
        PROP_PROJECT: {"select": {"name": option_name(project)} if project else None},
        PROP_LABELS: {"multi_select": [{"name": name} for name in labels]},
        PROP_PRIORITY: {"select": {"name": priority}},
        PROP_COMPLETED: {"checkbox": False},
        PROP_LINK: {"url": task_url(task["id"])},
        PROP_TODOIST_ID: {"rich_text": rich_text(task["id"])},
    }


def _date_key(value: dict[str, Any] | None) -> Any:
    """Normalize a date so that the instant is compared, not its representation."""
    if not value or not value.get("start"):
        return None
    start: str = value["start"]
    if "T" not in start:
        return start
    parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        tz = value.get("time_zone")
        parsed = parsed.replace(tzinfo=ZoneInfo(tz) if tz else ZoneInfo("UTC"))
    return parsed.timestamp()


def normalize(prop: dict[str, Any]) -> Any:
    """Reduce a property (from a request or a response) to a comparable value."""
    if "type" in prop:  # property as Notion returns it
        prop = {prop["type"]: prop[prop["type"]]}
    if "title" in prop:
        return plain_text(prop["title"])
    if "rich_text" in prop:
        return plain_text(prop["rich_text"])
    if "date" in prop:
        return _date_key(prop["date"])
    if "select" in prop:
        return (prop["select"] or {}).get("name")
    if "multi_select" in prop:
        return sorted(o["name"] for o in prop["multi_select"] or [])
    if "checkbox" in prop:
        return bool(prop["checkbox"])
    if "url" in prop:
        return prop["url"] or None
    raise ValueError(f"Unsupported property type: {prop}")


def changed_properties(desired: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    return {
        name: value
        for name, value in desired.items()
        if name not in current or normalize(value) != normalize(current[name])
    }


# --- Notion → Todoist ---

PRIORITY_VALUES = {label: value for value, label in PRIORITY_LABELS.items()}


@dataclass(frozen=True)
class TodoistUpdate:
    fields: dict[str, Any] = field(default_factory=dict)  # body of POST /tasks/{id}
    project_id: str | None = None  # set when the task has to move to another project
    new_project: str | None = None  # project to create in Todoist and move the task into
    completed: bool | None = None  # True: close the task; False: reopen it


def date_to_todoist(value: dict[str, Any] | None) -> dict[str, Any]:
    """Convert a Notion date into Todoist `due_*` fields.

    Dates with a time are sent in UTC; Todoist stores them with a fixed time zone.
    """
    if not value or not value.get("start"):
        return {"due_string": "no date"}
    start: str = value["start"]
    if "T" not in start:
        return {"due_date": start}
    parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        tz = value.get("time_zone")
        parsed = parsed.replace(tzinfo=ZoneInfo(tz) if tz else UTC)
    return {"due_datetime": parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


def find_project_id(name: str, projects: dict[str, str]) -> str | None:
    # Case-insensitive, so "work" in Notion reuses the "Work" project in Todoist.
    wanted = option_name(name).casefold()
    return next(
        (pid for pid, project in projects.items() if option_name(project).casefold() == wanted),
        None,
    )


def properties_to_todoist(changes: dict[str, Any], projects: dict[str, str]) -> TodoistUpdate:
    """Translate Notion properties edited by the user into Todoist changes.

    `projects` maps Todoist project IDs to names. A project that does not exist
    in Todoist is returned in `new_project`, to be created before moving the
    task. Raises ValueError when a value cannot be taken to Todoist.
    """
    fields: dict[str, Any] = {}
    project_id = None
    new_project = None
    completed = None

    for name, prop in changes.items():
        value = normalize(prop)
        if name == PROP_NAME:
            if not value.strip():
                raise ValueError("the title cannot be empty")
            fields["content"] = value
        elif name == PROP_DESCRIPTION:
            fields["description"] = value
        elif name == PROP_DATE:
            fields |= date_to_todoist(prop["date"])
        elif name == PROP_LABELS:
            fields["labels"] = value
        elif name == PROP_PRIORITY:
            fields["priority"] = PRIORITY_VALUES.get(value, 1)
        elif name == PROP_PROJECT:
            # No project in Notion means the Todoist inbox.
            project_name = value or INBOX_NAME
            project_id = find_project_id(project_name, projects)
            if project_id is None:
                new_project = project_name
        elif name == PROP_COMPLETED:
            completed = value
        else:
            raise ValueError(f"property '{name}' cannot be edited from Notion")

    return TodoistUpdate(fields, project_id, new_project, completed)
