from scripts.projects import (
    MAX_REMEMBERED,
    deleted_in_notion,
    missing_options,
    project_key,
    read_remembered,
    remember,
    stale_projects,
)


def test_stale_projects_are_the_ones_missing_in_todoist():
    options = ["Inbox", "Work", "Deleted"]
    assert stale_projects(options, {"p1": "Inbox", "p2": "Work"}, set()) == ["Deleted"]


def test_stale_projects_ignore_case_and_commas():
    options = ["work", "Home  garden"]
    assert stale_projects(options, {"p1": "Work", "p2": "Home, garden"}, set()) == []


def test_projects_being_created_from_notion_are_not_stale():
    assert stale_projects(["New"], {"p1": "Inbox"}, {"New"}) == []


def test_remembered_project_without_option_was_deleted_in_notion():
    remembered = {project_key("Work")}
    assert deleted_in_notion({"p2": "Work"}, [], remembered) == {"p2": "Work"}


def test_project_not_remembered_is_not_deleted():
    assert deleted_in_notion({"p2": "Work"}, [], set()) == {}


def test_project_with_option_is_not_deleted():
    assert deleted_in_notion({"p2": "Work"}, ["work"], {project_key("Work")}) == {}


def test_missing_options_are_the_todoist_projects_without_one():
    assert missing_options({"p1": "Inbox", "p2": "Work, home"}, ["Inbox"]) == ["Work  home"]


def test_remember_round_trip():
    description, complete = remember(["Inbox", "Work"])
    assert complete
    assert len(description) <= 280
    assert read_remembered(description) == {project_key("Inbox"), project_key("Work")}


def test_remember_reports_when_not_everything_fits():
    description, complete = remember([f"Project {i}" for i in range(MAX_REMEMBERED + 1)])
    assert not complete
    assert len(description) <= 280
