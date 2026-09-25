"""Notion database views created by setup_notion_db.py."""

from __future__ import annotations

from typing import Any

from .schema import (
    PROP_COMPLETED,
    PROP_DATE,
    PROP_DESCRIPTION,
    PROP_LABELS,
    PROP_LINK,
    PROP_NAME,
    PROP_PRIORITY,
    PROP_PROJECT,
)
from .todoist import INBOX_NAME

PENDING = {"property": PROP_COMPLETED, "checkbox": {"equals": False}}
BY_PRIORITY_AND_DATE = [
    {"property": PROP_PRIORITY, "direction": "ascending"},  # option order: P1 → P4
    {"property": PROP_DATE, "direction": "ascending"},
]


def _properties(schema: dict[str, Any], visible: list[str]) -> list[dict[str, Any]]:
    """Show the `Completed` checkbox followed by the `visible` properties, in that
    order, and hide the rest.

    The checkbox comes first in every view so that a task can be completed from
    anywhere in Notion.
    """
    visible = [PROP_COMPLETED, *visible]
    shown = [
        {"property_id": schema[name]["id"], "visible": True}
        | ({"date_format": "relative"} if name == PROP_DATE else {})
        for name in visible
    ]
    hidden = [
        {"property_id": prop["id"], "visible": False}
        for name, prop in schema.items()
        if name not in visible
    ]
    return shown + hidden


def _group_by_project(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "select",
        "property_id": schema[PROP_PROJECT]["id"],
        "sort": {"type": "ascending"},
        "hide_empty_groups": True,
    }


def desired_views(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Views in the order they should appear. Each view is identified by its name."""
    return [
        {
            "name": "📋 By project",
            "type": "table",
            "filter": PENDING,
            "sorts": BY_PRIORITY_AND_DATE,
            "configuration": {
                "type": "table",
                "group_by": _group_by_project(schema),
                "properties": _properties(
                    schema,
                    [PROP_NAME, PROP_PRIORITY, PROP_DATE, PROP_LABELS, PROP_DESCRIPTION, PROP_LINK],
                ),
                "wrap_cells": True,
            },
        },
        {
            "name": "📥 Inbox",
            "type": "list",
            "filter": {
                "and": [PENDING, {"property": PROP_PROJECT, "select": {"equals": INBOX_NAME}}]
            },
            "sorts": BY_PRIORITY_AND_DATE,
            "configuration": {
                "type": "list",
                "properties": _properties(schema, [PROP_NAME, PROP_PRIORITY, PROP_DATE, PROP_LABELS]),
            },
        },
        {
            "name": "🗂️ Projects",
            "type": "board",
            "filter": {
                "and": [
                    PENDING,
                    {"property": PROP_PROJECT, "select": {"does_not_equal": INBOX_NAME}},
                ]
            },
            "sorts": BY_PRIORITY_AND_DATE,
            "configuration": {
                "type": "board",
                "group_by": _group_by_project(schema),
                "properties": _properties(schema, [PROP_NAME, PROP_PRIORITY, PROP_DATE, PROP_LABELS]),
            },
        },
        {
            "name": "📅 Calendar",
            "type": "calendar",
            "filter": PENDING,
            "configuration": {
                "type": "calendar",
                "date_property_id": schema[PROP_DATE]["id"],
                "properties": _properties(schema, [PROP_NAME, PROP_PRIORITY, PROP_PROJECT]),
            },
        },
        {
            "name": "✅ Completed",
            "type": "table",
            "filter": {"property": PROP_COMPLETED, "checkbox": {"equals": True}},
            "sorts": [{"property": PROP_DATE, "direction": "descending"}],
            "configuration": {
                "type": "table",
                "properties": _properties(schema, [PROP_NAME, PROP_PROJECT, PROP_DATE, PROP_LINK]),
            },
        },
        {
            "name": "📊 Completed by project",
            "type": "chart",
            "filter": {"property": PROP_COMPLETED, "checkbox": {"equals": True}},
            "configuration": {
                "type": "chart",
                "chart_type": "bar",
                "x_axis": _group_by_project(schema),
                "y_axis": {"aggregator": "count"},
                "sort": "y_descending",
                "color_theme": "colorful",
                "show_data_labels": True,
                "axis_labels": "none",
                "grid_lines": "none",
            },
        },
    ]
