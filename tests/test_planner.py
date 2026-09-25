from scripts.planner import ActionKind, plan_sync
from scripts.properties import task_to_properties
from scripts.schema import PROP_COMPLETED, PROP_DATE, PROP_NAME, PROP_PRIORITY, PROP_PROJECT, PROP_SYNC
from scripts.snapshot import TRACKED, fingerprint, read_snapshot, snapshot_property

PROJECTS = {"p1": "Inbox"}


def task(task_id="1", content="Buy bread", priority=1):
    return {"id": task_id, "content": content, "project_id": "p1", "priority": priority}


def page_for(t, synced=True, **overrides):
    """Page as the last sync left it, with optional edits made in Notion."""
    props = task_to_properties(t, PROJECTS, None)
    if synced:
        props[PROP_SYNC] = snapshot_property({n: fingerprint(props[n]) for n in TRACKED})
    return {"id": f"page-{t['id']}", "properties": props | overrides}


def test_new_task_is_created_with_fingerprint():
    plan = plan_sync([task()], PROJECTS, {}, None)
    [action] = plan.actions
    assert action.kind is ActionKind.CREATE
    assert set(read_snapshot(action.properties)) == set(TRACKED)


def test_synced_page_without_changes():
    t = task()
    plan = plan_sync([t], PROJECTS, {"1": page_for(t)}, None)
    assert plan.actions == []
    assert plan.unchanged == 1


def test_old_page_without_fingerprint_gets_one():
    t = task()
    plan = plan_sync([t], PROJECTS, {"1": page_for(t, synced=False)}, None)
    [action] = plan.actions
    assert action.kind is ActionKind.UPDATE
    assert list(action.properties) == [PROP_SYNC]


def test_notion_edit_is_planned_for_todoist():
    t = task()
    page = page_for(t, **{PROP_PRIORITY: {"select": {"name": "P1"}}})
    plan = plan_sync([t], PROJECTS, {"1": page}, None)
    assert plan.actions == []
    [change] = plan.todoist_changes
    assert change.todoist_id == "1"
    assert list(change.fields) == [PROP_PRIORITY]


def test_conflict_is_recorded_and_todoist_wins():
    old = task(content="Buy bread")
    page = page_for(old, **{PROP_NAME: {"title": [{"plain_text": "Edited in Notion"}]}})
    new = task(content="Edited in Todoist")
    plan = plan_sync([new], PROJECTS, {"1": page}, None)
    assert plan.conflicts == {"1": [PROP_NAME]}
    assert plan.todoist_changes == []
    [action] = plan.actions
    assert action.properties[PROP_NAME]["title"][0]["text"]["content"] == "Edited in Todoist"


def test_task_no_longer_active_is_marked_completed():
    t = task()
    plan = plan_sync([], PROJECTS, {"1": page_for(t)}, None)
    [action] = plan.actions
    assert action.kind is ActionKind.COMPLETE
    assert action.properties[PROP_COMPLETED] == {"checkbox": True}


def test_already_completed_page_without_changes():
    t = task()
    done = page_for(t)
    done["properties"][PROP_COMPLETED] = {"checkbox": True}
    done["properties"][PROP_SYNC] = snapshot_property(
        {n: fingerprint(done["properties"][n]) for n in TRACKED}
    )
    plan = plan_sync([], PROJECTS, {"1": done}, None)
    assert plan.actions == []
    assert plan.unchanged == 1


def unlinked(title="New", **props):
    """Page created by hand in Notion, without a Todoist ID."""
    base = {
        PROP_NAME: {"title": [{"plain_text": title}]},
        PROP_PROJECT: {"select": None},
        PROP_PRIORITY: {"select": None},
        PROP_DATE: {"date": None},
        PROP_COMPLETED: {"checkbox": False},
    }
    return {"id": "new", "properties": base | props}


def test_page_without_todoist_id_is_created_in_todoist():
    page = unlinked(**{PROP_PRIORITY: {"select": {"name": "P2"}}})
    [new] = plan_sync([], PROJECTS, {}, None, [page]).new_tasks
    assert new.page_id == "new"
    assert new.fields == {"content": "New", "priority": 3, "project_id": "p1"}


def test_new_page_with_due_date():
    page = unlinked(**{PROP_DATE: {"date": {"start": "2026-10-01"}}})
    [new] = plan_sync([], PROJECTS, {}, None, [page]).new_tasks
    assert new.fields["due_date"] == "2026-10-01"


def test_empty_or_completed_new_pages_are_ignored():
    pages = [unlinked(title="  "), unlinked(**{PROP_COMPLETED: {"checkbox": True}})]
    assert plan_sync([], PROJECTS, {}, None, pages).new_tasks == []


def test_new_page_with_unknown_project_plans_the_project():
    page = unlinked(**{PROP_PROJECT: {"select": {"name": "Missing"}}})
    [new] = plan_sync([], PROJECTS, {}, None, [page]).new_tasks
    assert new.new_project == "Missing"
    assert new.fields["project_id"] is None


def test_edit_is_translated_into_a_todoist_update():
    t = task()
    page = page_for(t, **{PROP_PRIORITY: {"select": {"name": "P1"}}})
    [change] = plan_sync([t], PROJECTS, {"1": page}, None).todoist_changes
    assert change.update.fields == {"priority": 4}
    assert change.error is None


def test_moving_to_unknown_project_plans_the_project():
    t = task()
    page = page_for(t, **{PROP_PROJECT: {"select": {"name": "Missing"}}})
    [change] = plan_sync([t], PROJECTS, {"1": page}, None).todoist_changes
    assert change.update.new_project == "Missing"
    assert change.update.project_id is None


def test_recurring_task_date_cannot_be_edited_from_notion():
    t = task() | {"due": {"date": "2026-09-25", "is_recurring": True}}
    page = page_for(t, **{PROP_DATE: {"date": {"start": "2026-10-01"}}})
    plan = plan_sync([t], PROJECTS, {"1": page}, None)
    assert plan.todoist_changes == []
    [action] = plan.actions
    assert action.properties[PROP_DATE] == {"date": {"start": "2026-09-25"}}


def test_completing_recurring_task_from_notion():
    t = task() | {"due": {"date": "2026-09-25", "is_recurring": True}}
    page = page_for(t, **{PROP_COMPLETED: {"checkbox": True}})
    [change] = plan_sync([t], PROJECTS, {"1": page}, None).todoist_changes
    assert change.recurring
    assert change.update.completed is True


def test_unchecking_a_closed_task_reopens_it():
    t = task()
    page = page_for(t)
    page["properties"][PROP_SYNC] = snapshot_property(
        {n: fingerprint(page["properties"][n]) for n in TRACKED}
        | {PROP_COMPLETED: fingerprint({"checkbox": True})}
    )
    [change] = plan_sync([], PROJECTS, {"1": page}, None).todoist_changes
    assert change.update.completed is False

