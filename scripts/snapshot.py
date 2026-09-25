"""Fingerprint of the last sync, used to tell which side changed each field.

Every page stores in its `Sync` property a short hash per field, computed from
the value the field had when the last sync finished. Comparing the current
Todoist and Notion values against that fingerprint shows which side changed
each field. When both sides changed it, Todoist wins.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .properties import normalize, plain_text, rich_text
from .schema import (
    PROP_COMPLETED,
    PROP_DATE,
    PROP_DESCRIPTION,
    PROP_LABELS,
    PROP_NAME,
    PROP_PRIORITY,
    PROP_PROJECT,
    PROP_SYNC,
)

# Fields that can be edited on both sides.
TRACKED = [
    PROP_NAME,
    PROP_DESCRIPTION,
    PROP_DATE,
    PROP_PROJECT,
    PROP_LABELS,
    PROP_PRIORITY,
    PROP_COMPLETED,
]

Snapshot = dict[str, str]


def fingerprint(prop: dict[str, Any]) -> str:
    value = json.dumps(normalize(prop), ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(value.encode()).hexdigest()[:10]


def read_snapshot(properties: dict[str, Any]) -> Snapshot | None:
    """Return the fingerprint stored in the page, or None if it has none or it is corrupt."""
    raw = plain_text(properties.get(PROP_SYNC, {}).get("rich_text"))
    if not raw:
        return None
    try:
        snapshot = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return snapshot if isinstance(snapshot, dict) else None


def snapshot_property(snapshot: Snapshot) -> dict[str, Any]:
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"rich_text": rich_text(raw)}


@dataclass(frozen=True)
class Reconciliation:
    to_notion: dict[str, Any]  # properties to write to Notion (including `Sync`)
    to_todoist: dict[str, Any]  # fields edited in Notion, with their Notion value
    conflicts: list[str]  # fields edited on both sides; the Todoist value is kept
    snapshot: Snapshot  # new fingerprint, not counting the `to_todoist` changes


def reconcile(
    todoist: dict[str, Any],
    notion: dict[str, Any],
    snapshot: Snapshot | None,
    locked: frozenset[str] = frozenset(),
) -> Reconciliation:
    """Decide, field by field, which side changed since the last sync.

    `todoist` holds the properties built from the Todoist task; only the fields
    it contains are compared. For the missing ones (for completed tasks only
    `Completed` is known) the fingerprint is kept.

    Without a previous fingerprint there is no way to tell which side changed,
    so Todoist wins, as in a one-way sync. Fields in `locked` cannot be changed
    from Notion: if they are edited there, the Todoist value is restored.
    """
    to_notion: dict[str, Any] = {}
    to_todoist: dict[str, Any] = {}
    conflicts: list[str] = []
    new_snapshot: Snapshot = {}

    for name, value in todoist.items():
        current = notion.get(name)
        if current is not None and normalize(value) == normalize(current):
            if name in TRACKED:
                new_snapshot[name] = fingerprint(value)
            continue
        if name not in TRACKED or name in locked or snapshot is None or name not in snapshot:
            to_notion[name] = value
            if name in TRACKED:
                new_snapshot[name] = fingerprint(value)
            continue

        todoist_changed = fingerprint(value) != snapshot[name]
        notion_changed = current is None or fingerprint(current) != snapshot[name]
        if todoist_changed:
            to_notion[name] = value
            new_snapshot[name] = fingerprint(value)
            if notion_changed:
                conflicts.append(name)
        else:
            # Only Notion changed. The fingerprint is kept until the change
            # reaches Todoist, so it is detected again if it is not sent.
            to_todoist[name] = current
            new_snapshot[name] = snapshot[name]

    # Fields Todoist did not provide (completed tasks): the fingerprint is kept,
    # so a change made in Notion is detected if the task is reopened.
    for name in TRACKED:
        if name in todoist:
            continue
        previous = snapshot.get(name) if snapshot else None
        if previous is not None:
            new_snapshot[name] = previous
        elif name in notion:
            new_snapshot[name] = fingerprint(notion[name])

    if new_snapshot != snapshot:
        to_notion[PROP_SYNC] = snapshot_property(new_snapshot)
    return Reconciliation(to_notion, to_todoist, conflicts, new_snapshot)
