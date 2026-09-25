import pytest

from scripts.properties import date_to_todoist, properties_to_todoist
from scripts.schema import (
    PROP_COMPLETED,
    PROP_DATE,
    PROP_DESCRIPTION,
    PROP_LABELS,
    PROP_NAME,
    PROP_PRIORITY,
    PROP_PROJECT,
)

PROJECTS = {"inbox-id": "Inbox", "work-id": "Work", "comma-id": "Home, garden"}


def test_all_day_date():
    assert date_to_todoist({"start": "2026-09-25"}) == {"due_date": "2026-09-25"}


def test_date_with_time_is_sent_in_utc():
    assert date_to_todoist({"start": "2026-09-25T12:00:00.000+02:00"}) == {
        "due_datetime": "2026-09-25T10:00:00Z"
    }


def test_floating_date_with_time_zone():
    value = {"start": "2026-01-15T12:00:00", "time_zone": "Europe/Madrid"}
    assert date_to_todoist(value) == {"due_datetime": "2026-01-15T11:00:00Z"}


def test_removing_the_date():
    assert date_to_todoist(None) == {"due_string": "no date"}


def test_text_priority_and_label_fields():
    update = properties_to_todoist(
        {
            PROP_NAME: {"title": [{"plain_text": "New"}]},
            PROP_DESCRIPTION: {"rich_text": [{"plain_text": "Details"}]},
            PROP_PRIORITY: {"select": {"name": "P1"}},
            PROP_LABELS: {"multi_select": [{"name": "b"}, {"name": "a"}]},
            PROP_DATE: {"date": {"start": "2026-09-25"}},
        },
        PROJECTS,
    )
    assert update.fields == {
        "content": "New",
        "description": "Details",
        "priority": 4,
        "labels": ["a", "b"],
        "due_date": "2026-09-25",
    }
    assert update.project_id is None
    assert update.completed is None


def test_moving_to_another_project():
    update = properties_to_todoist({PROP_PROJECT: {"select": {"name": "Work"}}}, PROJECTS)
    assert update.project_id == "work-id"
    assert update.fields == {}


def test_project_with_comma_is_found_by_its_notion_name():
    update = properties_to_todoist({PROP_PROJECT: {"select": {"name": "Home  garden"}}}, PROJECTS)
    assert update.project_id == "comma-id"


def test_no_project_goes_to_inbox():
    update = properties_to_todoist({PROP_PROJECT: {"select": None}}, PROJECTS)
    assert update.project_id == "inbox-id"


def test_unknown_project_is_returned_as_new_project():
    update = properties_to_todoist({PROP_PROJECT: {"select": {"name": "Other"}}}, PROJECTS)
    assert update.new_project == "Other"
    assert update.project_id is None


def test_project_name_is_matched_ignoring_case():
    update = properties_to_todoist({PROP_PROJECT: {"select": {"name": "work"}}}, PROJECTS)
    assert update.project_id == "work-id"
    assert update.new_project is None


def test_empty_title_raises():
    with pytest.raises(ValueError, match="empty"):
        properties_to_todoist({PROP_NAME: {"title": []}}, PROJECTS)


def test_close_and_reopen():
    assert properties_to_todoist({PROP_COMPLETED: {"checkbox": True}}, PROJECTS).completed is True
    assert properties_to_todoist({PROP_COMPLETED: {"checkbox": False}}, PROJECTS).completed is False
