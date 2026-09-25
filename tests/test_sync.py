import requests

from scripts.planner import plan_sync
from scripts.properties import task_to_properties
from scripts.schema import (
    PROP_COMPLETED,
    PROP_NAME,
    PROP_PRIORITY,
    PROP_PROJECT,
    PROP_SYNC,
    PROP_TODOIST_ID,
)
from scripts.snapshot import TRACKED, fingerprint, read_snapshot, snapshot_property
from scripts.projects import project_key, read_remembered
from scripts.sync import Stats, apply_todoist_changes, create_todoist_tasks, sync_projects

PROJECTS = {"p1": "Inbox"}


class FakeTodoist:
    def __init__(self, fail=False, tasks_by_project=None):
        self.calls = []
        self.fail = fail
        self.tasks_by_project = tasks_by_project or {}

    def _record(self, *call):
        if self.fail:
            raise requests.HTTPError("HTTP 500")
        self.calls.append(call)

    def update_task(self, task_id, fields):
        self._record("update", task_id, fields)

    def move_task(self, task_id, project_id):
        self._record("move", task_id, project_id)

    def close_task(self, task_id):
        self._record("close", task_id)

    def reopen_task(self, task_id):
        self._record("reopen", task_id)

    def get_active_tasks(self, project_id=None):
        return self.tasks_by_project.get(project_id, [])

    def delete_project(self, project_id):
        self._record("delete_project", project_id)

    def create_project(self, name):
        self._record("create_project", name)
        return {"id": f"{name}-id", "name": name}

    def create_task(self, fields):
        self._record("create", fields)
        return {"id": "new-id", "project_id": "p1", "priority": 1} | fields

    def delete_task(self, task_id):
        self.calls.append(("delete", task_id))


class FakeNotion:
    def __init__(self, fail=False, project_options=(), remembered=()):
        self.updates = []
        self.fail = fail
        self.options = [{"id": f"{name}-opt", "name": name} for name in project_options]
        self.description = "".join(project_key(name) for name in remembered)
        self.schema_updates = []

    def get_schema(self):
        return {
            PROP_PROJECT: {"select": {"options": self.options}},
            PROP_SYNC: {"description": self.description},
        }

    def update_schema(self, properties):
        self.schema_updates.append(properties)

    def update_page(self, page_id, properties):
        if self.fail:
            raise requests.HTTPError("HTTP 502")
        self.updates.append((page_id, properties))


def synced_page(task, **overrides):
    props = task_to_properties(task, PROJECTS, None)
    props[PROP_SYNC] = snapshot_property({n: fingerprint(props[n]) for n in TRACKED})
    return {"id": "page-1", "properties": props | overrides}


TASK = {"id": "1", "content": "Buy bread", "project_id": "p1", "priority": 1}


def test_sends_to_todoist_and_updates_fingerprint():
    page = synced_page(TASK, **{PROP_PRIORITY: {"select": {"name": "P1"}}})
    plan = plan_sync([TASK], PROJECTS, {"1": page}, None)
    todoist, stats = FakeTodoist(), Stats(unchanged=plan.unchanged)

    [action] = apply_todoist_changes(plan, todoist, dry_run=False, stats=stats)

    assert todoist.calls == [("update", "1", {"priority": 4})]
    assert stats.sent_todoist == 1
    assert stats.unchanged == 0
    assert list(action.properties) == [PROP_SYNC]
    assert read_snapshot(action.properties)[PROP_PRIORITY] == fingerprint({"select": {"name": "P1"}})


def test_fingerprint_is_kept_when_todoist_fails():
    page = synced_page(TASK, **{PROP_PRIORITY: {"select": {"name": "P1"}}})
    plan = plan_sync([TASK], PROJECTS, {"1": page}, None)
    stats = Stats()

    actions = apply_todoist_changes(plan, FakeTodoist(fail=True), dry_run=False, stats=stats)

    assert actions == []
    assert stats.sent_todoist == 0
    assert len(stats.errors) == 1


def test_dry_run_does_not_call_todoist():
    page = synced_page(TASK, **{PROP_COMPLETED: {"checkbox": True}})
    plan = plan_sync([TASK], PROJECTS, {"1": page}, None)
    todoist = FakeTodoist()

    apply_todoist_changes(plan, todoist, dry_run=True, stats=Stats())

    assert todoist.calls == []


def test_completing_recurring_task_unchecks_it_in_notion():
    task = TASK | {"due": {"date": "2026-09-25", "is_recurring": True}}
    page = synced_page(task, **{PROP_COMPLETED: {"checkbox": True}})
    plan = plan_sync([task], PROJECTS, {"1": page}, None)
    todoist = FakeTodoist()

    [action] = apply_todoist_changes(plan, todoist, dry_run=False, stats=Stats())

    assert todoist.calls == [("close", "1")]
    assert action.properties[PROP_COMPLETED] == {"checkbox": False}
    assert read_snapshot(action.properties)[PROP_COMPLETED] == fingerprint({"checkbox": False})


def test_reopens_before_editing():
    page = synced_page(TASK)
    page["properties"][PROP_SYNC] = snapshot_property(
        {n: fingerprint(page["properties"][n]) for n in TRACKED}
        | {PROP_COMPLETED: fingerprint({"checkbox": True})}
    )
    plan = plan_sync([], PROJECTS, {"1": page}, None)
    todoist = FakeTodoist()

    apply_todoist_changes(plan, todoist, dry_run=False, stats=Stats())

    assert todoist.calls == [("reopen", "1")]


NEW_PAGE = {
    "id": "new-page",
    "properties": {
        PROP_NAME: {"title": [{"plain_text": "New"}]},
        PROP_COMPLETED: {"checkbox": False},
    },
}


def test_creates_in_todoist_and_links_the_page():
    plan = plan_sync([], PROJECTS, {}, None, [NEW_PAGE])
    todoist, notion, stats = FakeTodoist(), FakeNotion(), Stats()

    create_todoist_tasks(plan, todoist, notion, dry_run=False, stats=stats)

    assert todoist.calls[0][0] == "create"
    [(page_id, props)] = notion.updates
    assert page_id == "new-page"
    assert props[PROP_TODOIST_ID]["rich_text"][0]["text"]["content"] == "new-id"
    assert set(read_snapshot(props)) == set(TRACKED)
    assert stats.created_todoist == 1


def test_created_task_is_deleted_when_linking_fails():
    plan = plan_sync([], PROJECTS, {}, None, [NEW_PAGE])
    todoist, stats = FakeTodoist(), Stats()

    create_todoist_tasks(plan, todoist, FakeNotion(fail=True), dry_run=False, stats=stats)

    assert todoist.calls[-1] == ("delete", "new-id")
    assert stats.created_todoist == 0
    assert len(stats.errors) == 1


def test_create_in_dry_run_calls_nothing():
    plan = plan_sync([], PROJECTS, {}, None, [NEW_PAGE])
    todoist, notion, stats = FakeTodoist(), FakeNotion(), Stats()

    create_todoist_tasks(plan, todoist, notion, dry_run=True, stats=stats)

    assert todoist.calls == [] and notion.updates == []
    assert stats.created_todoist == 1


def test_moving_to_a_new_project_creates_it_once():
    tasks = [TASK, TASK | {"id": "2", "content": "Buy milk"}]
    garden = {PROP_PROJECT: {"select": {"name": "Garden"}}}
    pages = {t["id"]: synced_page(t, **garden) | {"id": f"page-{t['id']}"} for t in tasks}
    plan = plan_sync(tasks, dict(PROJECTS), pages, None)
    todoist, stats = FakeTodoist(), Stats(unchanged=plan.unchanged)

    apply_todoist_changes(plan, todoist, dry_run=False, stats=stats)

    assert todoist.calls == [
        ("create_project", "Garden"),
        ("move", "1", "Garden-id"),
        ("move", "2", "Garden-id"),
    ]
    assert stats.projects_created == 1


def test_new_page_with_new_project_creates_the_project_first():
    page = {
        "id": "new-page",
        "properties": NEW_PAGE["properties"] | {PROP_PROJECT: {"select": {"name": "Garden"}}},
    }
    plan = plan_sync([], dict(PROJECTS), {}, None, [page])
    todoist, notion, stats = FakeTodoist(), FakeNotion(), Stats()

    create_todoist_tasks(plan, todoist, notion, dry_run=False, stats=stats)

    assert todoist.calls[0] == ("create_project", "Garden")
    assert todoist.calls[1] == ("create", {"content": "New", "project_id": "Garden-id"})
    [(_, props)] = notion.updates
    assert "Project" not in props  # Todoist now has the same project name as Notion


def test_dry_run_does_not_create_projects():
    page = {
        "id": "new-page",
        "properties": NEW_PAGE["properties"] | {PROP_PROJECT: {"select": {"name": "Garden"}}},
    }
    plan = plan_sync([], dict(PROJECTS), {}, None, [page])
    todoist, stats = FakeTodoist(), Stats()

    create_todoist_tasks(plan, todoist, FakeNotion(), dry_run=True, stats=stats)

    assert todoist.calls == []
    assert stats.projects_created == 1



def options_sent(notion):
    [update] = notion.schema_updates
    return update[PROP_PROJECT]["select"]["options"]


def test_project_deleted_in_notion_is_deleted_in_todoist():
    plan = plan_sync([], {"p1": "Inbox", "p2": "Work"}, {}, None)
    todoist = FakeTodoist()
    notion = FakeNotion(project_options=["Inbox"], remembered=["Inbox", "Work"])
    stats = Stats()

    sync_projects(plan, todoist, notion, dry_run=False, stats=stats)

    assert todoist.calls == [("delete_project", "p2")]
    assert stats.projects_deleted == 1
    assert options_sent(notion) == [{"id": "Inbox-opt"}]


def test_project_deleted_in_notion_with_tasks_is_kept_and_retried():
    plan = plan_sync([], {"p1": "Inbox", "p2": "Work"}, {}, None)
    todoist = FakeTodoist(tasks_by_project={"p2": [{"id": "1"}]})
    notion = FakeNotion(project_options=["Inbox"], remembered=["Inbox", "Work"])

    sync_projects(plan, todoist, notion, dry_run=False, stats=Stats())

    assert todoist.calls == []
    assert options_sent(notion) == [{"id": "Inbox-opt"}]  # not added back
    description = notion.schema_updates[0][PROP_SYNC]["description"]
    assert project_key("Work") in read_remembered(description)


def test_new_todoist_project_is_added_to_notion_not_deleted():
    plan = plan_sync([], {"p1": "Inbox", "p2": "Brand new"}, {}, None)
    todoist = FakeTodoist()
    notion = FakeNotion(project_options=["Inbox"], remembered=["Inbox"])
    stats = Stats()

    sync_projects(plan, todoist, notion, dry_run=False, stats=stats)

    assert todoist.calls == []
    assert options_sent(notion) == [{"id": "Inbox-opt"}, {"name": "Brand new"}]
    assert stats.projects_added == 1


def test_project_renamed_in_todoist_is_not_deleted():
    # "Work" was renamed to "Office" in Todoist: the old option goes, the new one comes.
    plan = plan_sync([], {"p1": "Inbox", "p2": "Office"}, {}, None)
    todoist = FakeTodoist()
    notion = FakeNotion(project_options=["Inbox", "Work"], remembered=["Inbox", "Work"])

    sync_projects(plan, todoist, notion, dry_run=False, stats=Stats())

    assert todoist.calls == []
    assert options_sent(notion) == [{"id": "Inbox-opt"}, {"name": "Office"}]


def test_inbox_is_never_deleted():
    plan = plan_sync([], {"p1": "Inbox"}, {}, None)
    todoist = FakeTodoist()
    notion = FakeNotion(project_options=[], remembered=["Inbox"])

    sync_projects(plan, todoist, notion, dry_run=False, stats=Stats())

    assert todoist.calls == []


def test_projects_deleted_in_todoist_are_removed_from_notion():
    plan = plan_sync([], {"p1": "Inbox", "p2": "Work"}, {}, None)
    notion = FakeNotion(project_options=["Inbox", "Work", "Deleted"], remembered=["Inbox", "Work"])
    stats = Stats()

    sync_projects(plan, FakeTodoist(), notion, dry_run=False, stats=stats)

    assert options_sent(notion) == [{"id": "Inbox-opt"}, {"id": "Work-opt"}]
    assert stats.projects_removed == 1


def test_project_whose_creation_failed_is_kept():
    page = {
        "id": "new-page",
        "properties": NEW_PAGE["properties"] | {PROP_PROJECT: {"select": {"name": "Garden"}}},
    }
    plan = plan_sync([], dict(PROJECTS), {}, None, [page])
    notion = FakeNotion(project_options=["Inbox", "Garden"])

    create_todoist_tasks(plan, FakeTodoist(fail=True), notion, dry_run=False, stats=Stats())
    sync_projects(plan, FakeTodoist(), notion, dry_run=False, stats=Stats())

    assert options_sent(notion) == [{"id": "Inbox-opt"}, {"id": "Garden-opt"}]


def test_dry_run_does_not_touch_projects():
    plan = plan_sync([], {"p1": "Inbox", "p2": "Work"}, {}, None)
    todoist = FakeTodoist()
    notion = FakeNotion(project_options=["Inbox", "Deleted"], remembered=["Inbox", "Work"])
    stats = Stats()

    sync_projects(plan, todoist, notion, dry_run=True, stats=stats)

    assert todoist.calls == [] and notion.schema_updates == []
    assert (stats.projects_deleted, stats.projects_removed) == (1, 1)
