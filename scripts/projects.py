"""Keeps the Todoist projects and the Notion `Project` options in step.

Deleting a project in Notion means deleting its select option. To tell that
apart from a project that is simply new in Todoist, the names of the options
that existed at the end of the last run are remembered as short hashes in the
description of the hidden `Sync` property, which Notion limits to 280 characters.
"""

from __future__ import annotations

import hashlib

from .properties import option_name
from .todoist import INBOX_NAME

DESCRIPTION_LIMIT = 280
KEY_LENGTH = 6
MAX_REMEMBERED = DESCRIPTION_LIMIT // KEY_LENGTH


def project_key(name: str) -> str:
    return hashlib.sha1(option_name(name).casefold().encode()).hexdigest()[:KEY_LENGTH]


def read_remembered(description: str | None) -> set[str]:
    text = description or ""
    return {text[i : i + KEY_LENGTH] for i in range(0, len(text), KEY_LENGTH)}


def remember(names: list[str]) -> tuple[str, bool]:
    """Encode the project names for the `Sync` description.

    Returns the description and whether every name fit in it. Projects that do
    not fit are not remembered, so they will never be deleted from Notion.
    """
    keys = sorted({project_key(name) for name in names})
    return "".join(keys[:MAX_REMEMBERED]), len(keys) <= MAX_REMEMBERED


def _names(names: list[str]) -> set[str]:
    return {option_name(name).casefold() for name in names}


def stale_projects(options: list[str], projects: dict[str, str], protected: set[str]) -> list[str]:
    """Return the Notion project options that no longer exist in Todoist.

    `protected` holds the projects this run is creating from Notion, which must
    not be removed even if creating them in Todoist failed.
    """
    keep = _names([*projects.values(), *protected])
    return [name for name in options if option_name(name).casefold() not in keep]


def deleted_in_notion(
    projects: dict[str, str], options: list[str], remembered: set[str]
) -> dict[str, str]:
    """Return the Todoist projects (ID → name) whose option was deleted in Notion.

    Only projects that Notion already had on the last run count: a project that
    is missing from Notion because it is new in Todoist, or was renamed there,
    is not remembered under its current name. The Inbox is never deleted.
    """
    present = _names(options)
    return {
        pid: name
        for pid, name in projects.items()
        if name != INBOX_NAME
        and option_name(name).casefold() not in present
        and project_key(name) in remembered
    }


def missing_options(projects: dict[str, str], options: list[str]) -> list[str]:
    """Return the Todoist projects that have no option in Notion yet, such as empty ones."""
    present = _names(options)
    names = {option_name(name) for name in projects.values()}
    return sorted(name for name in names if name.casefold() not in present)
