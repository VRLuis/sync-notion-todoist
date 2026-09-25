"""Runs the sync: reads both sides, plans the changes and applies them."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

import requests

from .config import Config, ConfigError
from .notion import NotionClient
from .planner import (
    Action,
    ActionKind,
    Plan,
    index_pages_by_todoist_id,
    plan_sync,
    todoist_id_of,
)
from .projects import deleted_in_notion, missing_options, read_remembered, remember, stale_projects
from .properties import TodoistUpdate, changed_properties, find_project_id, task_to_properties
from .schema import PROP_COMPLETED, PROP_PROJECT, PROP_SYNC, schema_problems
from .snapshot import TRACKED, fingerprint, snapshot_property
from .todoist import TodoistClient

log = logging.getLogger(__name__)


@dataclass
class Stats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    completed: int = 0
    sent_todoist: int = 0
    created_todoist: int = 0
    projects_created: int = 0
    projects_removed: int = 0
    projects_added: int = 0
    projects_deleted: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.created} created, {self.updated} updated, {self.unchanged} unchanged, "
            f"{self.completed} marked as completed, "
            f"{self.sent_todoist} sent to Todoist, {self.created_todoist} created in Todoist, "
            f"{self.projects_created} projects created in Todoist, "
            f"{self.projects_removed} projects removed from Notion, "
            f"{self.projects_added} projects added to Notion, "
            f"{self.projects_deleted} projects deleted from Todoist, "
            f"{self.conflicts} conflicts, "
            f"{len(self.errors)} errors"
        )


def build_plan(todoist: TodoistClient, notion: NotionClient) -> Plan:
    user_tz = todoist.get_user_timezone()
    projects = todoist.get_project_names()
    tasks = todoist.get_active_tasks()
    log.info("Todoist: %d active tasks in %d projects", len(tasks), len(projects))

    problems = schema_problems(notion.get_schema())
    if problems:
        raise ConfigError(
            "The Notion database does not have the expected schema "
            "(run setup_notion_db.py): " + "; ".join(problems)
        )
    all_pages = notion.query_all_pages()
    pages = index_pages_by_todoist_id(all_pages)
    unlinked = [page for page in all_pages if not todoist_id_of(page)]
    log.info("Notion: %d pages with a Todoist ID, %d without one", len(pages), len(unlinked))

    return plan_sync(tasks, projects, pages, user_tz, unlinked)


def apply_todoist_changes(
    plan: Plan, todoist: TodoistClient, dry_run: bool, stats: Stats
) -> list[Action]:
    """Send the Notion edits to Todoist and return the Notion actions with the
    updated fingerprints.

    A task's fingerprint is only updated when Todoist accepted all of its
    changes; otherwise they are detected and sent again on the next run.
    """
    prefix = "[dry-run] " if dry_run else ""
    actions = {action.page_id: action for action in plan.actions if action.page_id}
    new_actions = [action for action in plan.actions if not action.page_id]

    for change in plan.todoist_changes:
        log.debug("%sSend to Todoist: %s (%s)", prefix, change.title, ", ".join(change.fields))
        if change.update is None:
            stats.errors.append(f"Task {change.todoist_id}: {change.error}")
            log.error(
                "Cannot send task %s to Todoist: %s", change.todoist_id, change.error
            )
            continue
        try:
            update = change.update
            if update.new_project:
                project_id = ensure_project(update.new_project, plan, todoist, dry_run, stats)
                update = replace(update, project_id=project_id, new_project=None)
            if not dry_run:
                apply_update(todoist, change.todoist_id, update)
        except requests.RequestException as exc:
            stats.errors.append(f"Send to Todoist: {exc}")
            log.debug("The previous send failed ('%s')", change.title)
            log.error("Failed to send task %s to Todoist: %s", change.todoist_id, exc)
            continue
        stats.sent_todoist += 1

        # Todoist now holds the Notion values, so the fingerprint becomes theirs.
        snapshot = change.snapshot | {name: fingerprint(v) for name, v in change.fields.items()}
        props: dict[str, Any] = {}
        if change.recurring and change.update.completed:
            # A recurring task is not closed: it stays active with its next date,
            # which reaches Notion on the next run.
            props[PROP_COMPLETED] = {"checkbox": False}
            snapshot[PROP_COMPLETED] = fingerprint(props[PROP_COMPLETED])
        props[PROP_SYNC] = snapshot_property(snapshot)

        existing = actions.get(change.page_id)
        if existing:
            actions[change.page_id] = replace(existing, properties=existing.properties | props)
        else:
            actions[change.page_id] = Action(ActionKind.UPDATE, change.title, props, change.page_id)
            stats.unchanged -= 1  # the plan counted it as unchanged in Notion

    return new_actions + list(actions.values())


def create_todoist_tasks(
    plan: Plan, todoist: TodoistClient, notion: NotionClient, dry_run: bool, stats: Stats
) -> None:
    """Create a Todoist task for each new Notion page and link the page to it.

    Todoist does not deduplicate creations, so each page is linked right after
    its task is created. If linking fails, the task is deleted so that it is
    not duplicated on the next run.
    """
    prefix = "[dry-run] " if dry_run else ""
    for new in plan.new_tasks:
        log.debug("%sCreate in Todoist: %s", prefix, new.title)
        if new.fields is None:
            stats.errors.append(f"Create in Todoist: {new.error}")
            log.error("Cannot create page %s in Todoist: %s", new.page_id, new.error)
            continue
        if dry_run:
            if new.new_project:
                ensure_project(new.new_project, plan, todoist, dry_run, stats)
            stats.created_todoist += 1
            continue

        try:
            fields = new.fields
            if new.new_project:
                project_id = ensure_project(new.new_project, plan, todoist, dry_run, stats)
                fields = fields | {"project_id": project_id}
            task = todoist.create_task(fields)
        except requests.RequestException as exc:
            stats.errors.append(f"Create in Todoist: {exc}")
            log.error("Failed to create page %s in Todoist: %s", new.page_id, exc)
            continue

        desired = task_to_properties(task, plan.projects, plan.user_tz)
        props = changed_properties(desired, new.properties)
        props[PROP_SYNC] = snapshot_property({name: fingerprint(desired[name]) for name in TRACKED})
        try:
            notion.update_page(new.page_id, props)
        except requests.RequestException as exc:
            stats.errors.append(f"Link new task: {exc}")
            log.error("Failed to link page %s to its task: %s", new.page_id, exc)
            try:
                todoist.delete_task(task["id"])
            except requests.RequestException as delete_exc:
                log.error(
                    "Could not delete task %s from Todoist; delete it by hand to avoid "
                    "a duplicate: %s", task["id"], delete_exc,
                )
            continue
        stats.created_todoist += 1


def ensure_project(
    name: str, plan: Plan, todoist: TodoistClient, dry_run: bool, stats: Stats
) -> str:
    """Return the ID of the Todoist project called `name`, creating it if it is missing.

    Created projects are added to `plan.projects`, so each one is created only
    once per run even when several tasks use it.
    """
    project_id = find_project_id(name, plan.projects)
    if project_id:
        return project_id

    log.debug("%sCreate project in Todoist: %s", "[dry-run] " if dry_run else "", name)
    stats.projects_created += 1
    if dry_run:
        project = {"id": f"dry-run:{name}", "name": name}
    else:
        project = todoist.create_project(name)
    plan.projects[project["id"]] = project["name"]
    return project["id"]


def sync_projects(
    plan: Plan, todoist: TodoistClient, notion: NotionClient, dry_run: bool, stats: Stats
) -> None:
    """Bring the Notion `Project` options and the Todoist projects in step.

    - A project whose option was deleted in Notion is deleted in Todoist, but
      only once it is empty: its pages lost their project, so earlier in this
      run its tasks were moved to the Inbox.
    - Options for projects that no longer exist in Todoist are removed.
    - Todoist projects without an option, such as empty ones, get one.

    Runs last, so the projects created and the tasks moved in this run are known.
    """
    prefix = "[dry-run] " if dry_run else ""
    schema = notion.get_schema()
    options = schema[PROP_PROJECT]["select"]["options"]
    names = [o["name"] for o in options]

    pending: list[str] = []  # deleted in Notion, but still has tasks in Todoist
    remembered = read_remembered(schema[PROP_SYNC]["description"])
    for project_id, name in deleted_in_notion(plan.projects, names, remembered).items():
        if todoist.get_active_tasks(project_id):
            log.warning("Project %s was deleted in Notion but still has tasks", project_id)
            pending.append(name)
            continue
        log.debug("%sDelete project from Todoist: %s", prefix, name)
        if not dry_run:
            todoist.delete_project(project_id)
        del plan.projects[project_id]
        stats.projects_deleted += 1

    protected = {change.update.new_project for change in plan.todoist_changes if change.update}
    protected |= {new.new_project for new in plan.new_tasks}
    protected.discard(None)
    stale = set(stale_projects(names, plan.projects, protected))
    missing = [n for n in missing_options(plan.projects, names) if n not in pending]
    for name in sorted(stale):
        log.debug("%sRemove project from Notion: %s", prefix, name)
    for name in missing:
        log.debug("%sAdd project to Notion: %s", prefix, name)
    stats.projects_removed = len(stale)
    stats.projects_added = len(missing)

    kept = [o for o in options if o["name"] not in stale]
    description, complete = remember([*(o["name"] for o in kept), *missing, *pending])
    if not complete:
        log.warning("Too many projects to remember; some cannot be deleted from Notion")
    if dry_run:
        return
    new_options = [{"id": o["id"]} for o in kept] + [{"name": name} for name in missing]
    notion.update_schema(
        {
            PROP_PROJECT: {"select": {"options": new_options}},
            PROP_SYNC: {"rich_text": {}, "description": description},
        }
    )


def apply_update(todoist: TodoistClient, task_id: str, update: TodoistUpdate) -> None:
    # Reopen before editing and close afterwards, so edits apply to an active task.
    if update.completed is False:
        todoist.reopen_task(task_id)
    if update.fields:
        todoist.update_task(task_id, update.fields)
    if update.project_id:
        todoist.move_task(task_id, update.project_id)
    if update.completed:
        todoist.close_task(task_id)


def apply_plan(plan: Plan, todoist: TodoistClient, notion: NotionClient, dry_run: bool) -> Stats:
    """Apply each action on its own, so one failure does not stop the rest.

    Todoist is written first and Notion afterwards, with the new fingerprints.
    Task titles are only logged at DEBUG level (-v), so they never show up in
    public GitHub Actions logs.
    """
    stats = Stats(unchanged=plan.unchanged, conflicts=len(plan.conflicts))
    prefix = "[dry-run] " if dry_run else ""

    # Todoist always wins: its value is already in the Notion actions.
    for todoist_id, fields in plan.conflicts.items():
        log.info(
            "Conflict on task %s (%s): keeping the Todoist value",
            todoist_id, ", ".join(fields),
        )
    create_todoist_tasks(plan, todoist, notion, dry_run, stats)

    for action in apply_todoist_changes(plan, todoist, dry_run, stats):
        detail = f" ({', '.join(action.properties)})" if action.kind is ActionKind.UPDATE else ""
        log.debug("%s%s: %s%s", prefix, action.kind.value, action.title, detail)

        try:
            if not dry_run:
                if action.page_id is None:
                    notion.create_page(action.properties)
                else:
                    notion.update_page(action.page_id, action.properties)
        except requests.RequestException as exc:
            stats.errors.append(f"{action.kind.value}: {exc}")
            log.debug("The previous action failed ('%s')", action.title)
            log.error("Failed to %s: %s", action.kind.value.lower(), exc)
            continue

        if action.kind is ActionKind.CREATE:
            stats.created += 1
        elif action.kind is ActionKind.UPDATE:
            stats.updated += 1
        else:
            stats.completed += 1

    try:
        sync_projects(plan, todoist, notion, dry_run, stats)
    except requests.RequestException as exc:
        stats.errors.append(f"Sync projects: {exc}")
        log.error("Failed to sync projects: %s", exc)

    return stats


def run(config: Config, dry_run: bool) -> Stats:
    todoist = TodoistClient(config.todoist_token)
    notion = NotionClient(config.notion_token, config.notion_database_id)
    return apply_plan(build_plan(todoist, notion), todoist, notion, dry_run)
