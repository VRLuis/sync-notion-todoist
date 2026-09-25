"""Decide which changes to make in Notion and in Todoist, without calling any API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from .properties import (
    TodoistUpdate,
    plain_text,
    properties_to_todoist,
    task_to_properties,
)
from .schema import PROP_COMPLETED, PROP_DATE, PROP_NAME, PROP_SYNC, PROP_TODOIST_ID
from .snapshot import (
    TRACKED,
    Reconciliation,
    Snapshot,
    fingerprint,
    read_snapshot,
    reconcile,
    snapshot_property,
)

log = logging.getLogger(__name__)


class ActionKind(Enum):
    CREATE = "Create"
    UPDATE = "Update"
    COMPLETE = "Mark as completed"


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    title: str
    properties: dict[str, Any]
    page_id: str | None = None  # None only for CREATE


@dataclass(frozen=True)
class TodoistChange:
    todoist_id: str
    title: str
    page_id: str
    fields: dict[str, Any]  # Notion properties edited by the user, with their new value
    snapshot: Snapshot  # page fingerprint, not counting these changes
    update: TodoistUpdate | None  # None when it cannot be sent (see `error`)
    error: str | None = None
    recurring: bool = False


@dataclass(frozen=True)
class NewTask:
    title: str
    page_id: str
    properties: dict[str, Any]  # current page properties
    fields: dict[str, Any] | None  # body of POST /tasks; None when it cannot be created
    error: str | None = None
    new_project: str | None = None  # project to create in Todoist before the task


@dataclass(frozen=True)
class Plan:
    actions: list[Action]
    unchanged: int
    todoist_changes: list[TodoistChange] = field(default_factory=list)
    conflicts: dict[str, list[str]] = field(default_factory=dict)  # Todoist ID → fields
    new_tasks: list[NewTask] = field(default_factory=list)
    # Needed to turn the tasks created in Todoist into Notion properties.
    projects: dict[str, str] = field(default_factory=dict)
    user_tz: ZoneInfo | None = None


def todoist_id_of(page: dict[str, Any]) -> str:
    return plain_text(page["properties"].get(PROP_TODOIST_ID, {}).get("rich_text"))


def index_pages_by_todoist_id(pages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for page in pages:
        todoist_id = todoist_id_of(page)
        if not todoist_id:
            continue
        if todoist_id in index:
            log.warning("Duplicate page for task %s (%s); ignoring it", todoist_id, page["id"])
            continue
        index[todoist_id] = page
    return index


def plan_sync(
    tasks: list[dict[str, Any]],
    projects: dict[str, str],
    pages: dict[str, dict[str, Any]],
    user_tz: ZoneInfo | None,
    unlinked_pages: list[dict[str, Any]] | None = None,
) -> Plan:
    """Compare every Todoist task with its Notion page and plan the changes on both sides.

    `pages` maps Todoist IDs to their Notion pages. `unlinked_pages` are the
    pages created in Notion that have no Todoist ID yet.
    """
    actions: list[Action] = []
    todoist_changes: list[TodoistChange] = []
    conflicts: dict[str, list[str]] = {}
    unchanged = 0

    def add(
        kind: ActionKind,
        todoist_id: str,
        title: str,
        page: dict[str, Any],
        r: Reconciliation,
        recurring: bool = False,
    ) -> None:
        nonlocal unchanged
        if r.to_notion:
            actions.append(Action(kind, title, r.to_notion, page["id"]))
        else:
            unchanged += 1
        if r.to_todoist:
            try:
                update, error = properties_to_todoist(r.to_todoist, projects), None
            except ValueError as exc:
                update, error = None, str(exc)
            todoist_changes.append(
                TodoistChange(
                    todoist_id=todoist_id,
                    title=title,
                    page_id=page["id"],
                    fields=r.to_todoist,
                    snapshot=r.snapshot,
                    update=update,
                    error=error,
                    recurring=recurring,
                )
            )
        if r.conflicts:
            conflicts[todoist_id] = r.conflicts

    for task in tasks:
        desired = task_to_properties(task, projects, user_tz)
        title = task.get("content", "")
        page = pages.get(task["id"])
        if page is None:
            snapshot = {name: fingerprint(desired[name]) for name in TRACKED}
            desired[PROP_SYNC] = snapshot_property(snapshot)
            actions.append(Action(ActionKind.CREATE, title, desired))
            continue
        # Changing the date of a recurring task would remove its recurrence.
        recurring = bool((task.get("due") or {}).get("is_recurring"))
        locked = frozenset({PROP_DATE}) if recurring else frozenset()
        props = page["properties"]
        r = reconcile(desired, props, read_snapshot(props), locked)
        add(ActionKind.UPDATE, task["id"], title, page, r, recurring)

    # Completed or deleted tasks no longer appear among the active ones; all
    # that is known about them is that they are completed.
    active_ids = {task["id"] for task in tasks}
    for todoist_id, page in pages.items():
        if todoist_id in active_ids:
            continue
        props = page["properties"]
        r = reconcile({PROP_COMPLETED: {"checkbox": True}}, props, read_snapshot(props))
        kind = ActionKind.COMPLETE if PROP_COMPLETED in r.to_notion else ActionKind.UPDATE
        add(kind, todoist_id, plain_text(props.get(PROP_NAME, {}).get("title")), page, r)

    new_tasks = [new_task(page, projects) for page in unlinked_pages or []]
    return Plan(
        actions,
        unchanged,
        todoist_changes,
        conflicts,
        [task for task in new_tasks if task is not None],
        projects,
        user_tz,
    )


def new_task(page: dict[str, Any], projects: dict[str, str]) -> NewTask | None:
    """Build the Todoist task for a page created in Notion.

    Pages without a title (the empty rows Notion leaves behind) and pages that
    are already marked as completed are ignored.
    """
    props = page["properties"]
    title = plain_text(props.get(PROP_NAME, {}).get("title")).strip()
    if not title or props.get(PROP_COMPLETED, {}).get("checkbox"):
        return None
    editable = {name: props[name] for name in TRACKED if name in props and name != PROP_COMPLETED}
    try:
        update = properties_to_todoist(editable, projects)
    except ValueError as exc:
        return NewTask(title, page["id"], props, None, str(exc))
    fields = update.fields | {"project_id": update.project_id}
    if fields.get("due_string") == "no date":
        del fields["due_string"]
    return NewTask(title, page["id"], props, fields, new_project=update.new_project)

