"""Expected schema of the Notion database."""

from __future__ import annotations

from typing import Any

PROP_NAME = "Name"
PROP_DESCRIPTION = "Description"
PROP_DATE = "Due date"
PROP_PROJECT = "Project"
PROP_LABELS = "Labels"
PROP_PRIORITY = "Priority"
PROP_COMPLETED = "Completed"
PROP_LINK = "Link"
PROP_TODOIST_ID = "Todoist ID"
PROP_SYNC = "Sync"  # fingerprint of the last sync (see snapshot.py)

EXPECTED_PROPERTIES = {
    PROP_NAME: "title",
    PROP_DESCRIPTION: "rich_text",
    PROP_DATE: "date",
    PROP_PROJECT: "select",
    PROP_LABELS: "multi_select",
    PROP_PRIORITY: "select",
    PROP_COMPLETED: "checkbox",
    PROP_LINK: "url",
    PROP_TODOIST_ID: "rich_text",
    PROP_SYNC: "rich_text",
}

# The Todoist API uses 4 for the highest priority, which the app shows as P1.
PRIORITY_LABELS = {4: "P1", 3: "P2", 2: "P3", 1: "P4"}
PRIORITY_COLORS = {"P1": "red", "P2": "orange", "P3": "blue", "P4": "gray"}


def schema_problems(properties: dict[str, Any]) -> list[str]:
    problems = []
    for name, expected in EXPECTED_PROPERTIES.items():
        actual = properties.get(name, {}).get("type")
        if actual is None:
            problems.append(f"missing property '{name}' ({expected})")
        elif actual != expected:
            problems.append(f"'{name}' is of type {actual}, expected {expected}")
    return problems


def _new_property(name: str, prop_type: str) -> dict[str, Any]:
    if name == PROP_PRIORITY:
        options = [{"name": label, "color": color} for label, color in PRIORITY_COLORS.items()]
        return {"select": {"options": options}}
    return {prop_type: {}}


def plan_schema_changes(current: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Work out the changes needed to complete the schema.

    Returns the properties to create or rename, and the errors that have to be
    fixed by hand. It never changes the type of an existing property, because
    that could lose data.
    """
    changes: dict[str, Any] = {}
    errors: list[str] = []

    # Every database has exactly one title property; if it has another name,
    # it is renamed instead of creating a second one.
    title_name = next(n for n, p in current.items() if p["type"] == "title")
    if title_name != PROP_NAME:
        if PROP_NAME in current:
            errors.append(f"'{PROP_NAME}' exists but is not the title property ('{title_name}' is)")
        else:
            changes[title_name] = {"name": PROP_NAME}

    for name, prop_type in EXPECTED_PROPERTIES.items():
        if prop_type == "title":
            continue
        existing = current.get(name)
        if existing is None:
            changes[name] = _new_property(name, prop_type)
        elif existing["type"] != prop_type:
            errors.append(f"'{name}' is of type {existing['type']}, expected {prop_type}")
    return changes, errors
