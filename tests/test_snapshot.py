from scripts.schema import PROP_COMPLETED, PROP_DATE, PROP_LINK, PROP_NAME, PROP_PRIORITY, PROP_SYNC
from scripts.snapshot import fingerprint, read_snapshot, reconcile, snapshot_property


def title(text):
    return {"title": [{"type": "text", "text": {"content": text}}]}


def select(name):
    return {"select": {"name": name}}


def checkbox(value):
    return {"checkbox": value}


def snap(**props):
    """Fingerprint of the values at the last sync."""
    return {name: fingerprint(value) for name, value in props.items()}


BASE = {PROP_NAME: title("Buy bread"), PROP_PRIORITY: select("P4"), PROP_COMPLETED: checkbox(False)}
BASE_SNAPSHOT = snap(**BASE)


def test_no_changes_writes_nothing():
    r = reconcile(BASE, BASE, BASE_SNAPSHOT)
    assert r.to_notion == {}
    assert r.to_todoist == {}
    assert r.conflicts == []


def test_todoist_change_goes_to_notion():
    todoist = BASE | {PROP_NAME: title("Buy wholemeal bread")}
    r = reconcile(todoist, BASE, BASE_SNAPSHOT)
    assert r.to_notion[PROP_NAME] == title("Buy wholemeal bread")
    assert r.to_todoist == {}
    assert read_snapshot(r.to_notion)[PROP_NAME] == fingerprint(title("Buy wholemeal bread"))


def test_notion_change_goes_to_todoist_and_keeps_fingerprint():
    notion = BASE | {PROP_PRIORITY: select("P1")}
    r = reconcile(BASE, notion, BASE_SNAPSHOT)
    assert r.to_todoist == {PROP_PRIORITY: select("P1")}
    # Notion is not overwritten and the fingerprint is kept, so it is detected again if not sent.
    assert r.to_notion == {}


def test_conflict_todoist_wins():
    todoist = BASE | {PROP_NAME: title("From Todoist")}
    notion = BASE | {PROP_NAME: title("From Notion")}
    r = reconcile(todoist, notion, BASE_SNAPSHOT)
    assert r.to_notion[PROP_NAME] == title("From Todoist")
    assert r.to_todoist == {}
    assert r.conflicts == [PROP_NAME]


def test_different_fields_on_each_side_are_not_a_conflict():
    todoist = BASE | {PROP_NAME: title("New name")}
    notion = BASE | {PROP_PRIORITY: select("P2")}
    r = reconcile(todoist, notion, BASE_SNAPSHOT)
    assert r.to_notion[PROP_NAME] == title("New name")
    assert r.to_todoist == {PROP_PRIORITY: select("P2")}
    assert r.conflicts == []


def test_without_fingerprint_todoist_wins_and_one_is_created():
    notion = BASE | {PROP_NAME: title("Edited in Notion")}
    r = reconcile(BASE, notion, None)
    assert r.to_notion[PROP_NAME] == BASE[PROP_NAME]
    assert r.to_todoist == {}
    assert read_snapshot(r.to_notion) == BASE_SNAPSHOT


def test_read_only_fields_always_come_from_todoist():
    todoist = BASE | {PROP_LINK: {"url": "https://app.todoist.com/app/task/1"}}
    notion = BASE | {PROP_LINK: {"url": "https://something.else"}}
    r = reconcile(todoist, notion, BASE_SNAPSHOT)
    assert r.to_notion == {PROP_LINK: todoist[PROP_LINK]}
    assert r.to_todoist == {}


def test_checking_completed_in_notion_goes_to_todoist():
    notion = BASE | {PROP_COMPLETED: checkbox(True)}
    r = reconcile(BASE, notion, BASE_SNAPSHOT)
    assert r.to_todoist == {PROP_COMPLETED: checkbox(True)}


def test_completed_task_only_compares_completed():
    # All that is known about a completed task is that it is completed; Notion
    # edits to other fields are not sent, but their fingerprint is kept.
    notion = BASE | {PROP_PRIORITY: select("P1")}
    r = reconcile({PROP_COMPLETED: checkbox(True)}, notion, BASE_SNAPSHOT)
    assert r.to_notion[PROP_COMPLETED] == checkbox(True)
    assert r.to_todoist == {}
    assert read_snapshot(r.to_notion)[PROP_PRIORITY] == BASE_SNAPSHOT[PROP_PRIORITY]


def test_unchecking_completed_in_notion_reopens_in_todoist():
    done = BASE | {PROP_COMPLETED: checkbox(True)}
    notion = BASE | {PROP_COMPLETED: checkbox(False)}
    r = reconcile({PROP_COMPLETED: checkbox(True)}, notion, snap(**done))
    assert r.to_todoist == {PROP_COMPLETED: checkbox(False)}
    assert r.to_notion == {}


def test_same_instant_in_another_format_is_not_a_change():
    todoist = BASE | {PROP_DATE: {"date": {"start": "2026-09-25T10:00:00.000000Z"}}}
    notion = BASE | {PROP_DATE: {"date": {"start": "2026-09-25T12:00:00.000+02:00"}}}
    r = reconcile(todoist, notion, snap(**todoist))
    assert r.to_notion == {}
    assert r.to_todoist == {}


def test_corrupt_fingerprint_is_treated_as_missing():
    props = {PROP_SYNC: {"rich_text": [{"plain_text": "not json"}]}}
    assert read_snapshot(props) is None


def test_fingerprint_round_trip():
    props = {PROP_SYNC: snapshot_property(BASE_SNAPSHOT)}
    assert read_snapshot(props) == BASE_SNAPSHOT
