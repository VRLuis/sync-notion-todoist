# Todoist ↔ Notion sync

Keeps your active Todoist tasks in sync with a Notion database, **in both directions**. Edits made in Todoist reach Notion, and edits made in Notion reach Todoist. When the same field is edited on both sides, **Todoist wins**. It runs every 5 minutes on GitHub Actions.

## What it does

**Todoist → Notion**

- Reads every active task through the [Todoist API v1](https://developer.todoist.com/api/v1/) (`/api/v1/tasks`, `/api/v1/projects`, `/api/v1/user`).
- Creates or updates one Notion page per task, identified by the `Todoist ID` property, so no duplicates are created.
- Only updates pages whose data changed, and each update only sends the changed properties.
- When a task stops being active because it was completed or deleted, its page is marked `Completed`. Pages are never deleted.
- Projects that no longer exist in Todoist (deleted or archived) are removed from the `Project` options in Notion, and from the pages that had them.

**Notion → Todoist**

- Edits to `Name`, `Description`, `Due date`, `Priority`, `Labels` and `Project` are sent to Todoist. Changing `Project` moves the task; an empty project means the Inbox.
- A project that does not exist in Todoist is created there first. Project names are matched ignoring case, so `work` in Notion uses the `Work` project in Todoist.
- Deleting a project option in Notion deletes the project in Todoist. Its pages lose their project, so their tasks are first moved to the Inbox; the project is only deleted once it is empty, so no active task is lost. Projects Notion has never seen, such as ones just created or renamed in Todoist, are never deleted. The Inbox is never deleted.
- Todoist projects without tasks also get an option in Notion, so they can be deleted from there too.
- Checking `Completed` closes the task; unchecking it reopens the task.
- A page created in Notion without a `Todoist ID` becomes a new Todoist task, and the page is linked to it. Pages without a title and pages created already completed are ignored.

**How it knows which side changed**

Each page stores a short hash per field in the hidden `Sync` property, taken when the last sync finished. On every run the current Todoist and Notion values are compared against it, field by field:

| Todoist  | Notion   | Result                                            |
|----------|----------|---------------------------------------------------|
| same     | same     | nothing                                           |
| changed  | same     | Todoist → Notion                                  |
| same     | changed  | Notion → Todoist                                  |
| changed  | changed  | conflict: the Todoist value is kept and logged    |

If Todoist rejects a change, the fingerprint is not updated, so the change is sent again on the next run.

**Other details**

- At most 3 requests per second to Notion. On `429` it waits for `Retry-After`, and it retries temporary errors with exponential backoff.
- Uses the Notion API with `Notion-Version: 2026-03-11` and the *data sources* model.
- If something fails, the script exits with a non-zero code and the workflow shows up in red.

## Code layout

```
sync.py                     # sync CLI (--dry-run, -v)
setup_notion_db.py          # CLI that prepares the database: properties and views
scripts/
├── config.py               # environment variables
├── http.py                 # HTTP client: request rate limit and retries with backoff
├── todoist.py              # Todoist API v1: read and write tasks, projects and time zone
├── notion.py               # Notion API: data source, queries, pages and views
├── schema.py               # expected properties, schema validation and creation
├── views.py                # views created by setup_notion_db.py
├── properties.py           # Todoist task ↔ Notion properties, and comparison
├── snapshot.py             # fingerprint of the last sync: which side changed each field
├── planner.py              # decides what to create, update or complete (no API calls)
└── sync.py                 # runs it all: reads, plans and applies the plan
tests/                      # tests for the decision logic
```

The decision logic (`planner.py`, `snapshot.py`, `properties.py` and `schema.py`) is pure: it makes no network calls, so it can be tested without tokens.

## Database properties

| Property    | Type         | Todoist field                                        | Editable from Notion |
|-------------|--------------|------------------------------------------------------|----------------------|
| Name        | Title        | `content`                                            | yes                  |
| Description | Rich text    | `description`                                        | yes                  |
| Due date    | Date         | `due.date` (with time and time zone when it has one) | yes, except recurring tasks |
| Project     | Select       | project name (`project_id`)                          | yes (moves the task; new projects are created) |
| Labels      | Multi-select | `labels`                                             | yes                  |
| Priority    | Select       | `priority` (4→P1, 3→P2, 2→P3, 1→P4)                  | yes                  |
| Completed   | Checkbox     | whether the task is still active                     | yes (close/reopen)   |
| Link        | URL          | `https://app.todoist.com/app/task/<id>`              | no                   |
| Todoist ID  | Rich text    | `id`                                                 | no                   |
| Sync        | Rich text    | fingerprint of the last sync (hidden in the views)   | no                   |

> API v1 no longer includes the `url` field on tasks, so the link is built with the format given in the official documentation.

The Todoist inbox is always synced as `Inbox`, whatever language your Todoist account uses, because the views filter on that name.

## Views

`setup_notion_db.py` creates these views in the database:

| View                       | Type     | Shows                                              |
|----------------------------|----------|----------------------------------------------------|
| 📋 By project              | Table    | pending tasks grouped by project                   |
| 📥 Inbox                   | List     | pending tasks in the Inbox                         |
| 🗂️ Projects                | Board    | one column per project, without the Inbox          |
| 📅 Calendar                | Calendar | pending tasks on their due date                    |
| ✅ Completed               | Table    | completed tasks, latest due date first             |
| 📊 Completed by project    | Chart    | number of completed tasks per project              |

Views are identified by name. Existing views are never changed, so you can adjust them in Notion; to restore one, delete it and run `setup_notion_db.py` again.

## Setup

### 1. Todoist token

1. Open Todoist → **Settings → Integrations → Developer**.
2. Copy the **API token**. That value is `TODOIST_API_TOKEN`.

### 2. Notion integration and database connection

1. Go to <https://www.notion.so/profile/integrations> and click **New integration**.
2. Choose the workspace and the **Internal** type, and save it.
3. In the **Capabilities** tab, enable *Read content*, *Update content* and *Insert content*.
4. Copy the **Internal Integration Secret** (it starts with `ntn_`). That value is `NOTION_TOKEN`.
5. Create the database in Notion if it does not exist yet. It can be empty, because the setup script creates the properties and views.
6. **Share the database with the integration.** Open the database as a full page, click the **•••** menu at the top right → **Connections** → **Add connections** and choose your integration. If you skip this step, the API returns `404 object_not_found`.

### 3. `NOTION_DATABASE_ID`

Open the database as a full page and click **Share → Copy link**. The URL looks like this:

```
https://www.notion.so/myworkspace/1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6?v=...
                                  └────────── DATABASE_ID ─────────┘
```

The ID is the 32 hexadecimal characters before `?v=`. Put only the ID in `.env`, not the whole URL. What comes after `v=` is the view ID, **not** the database ID.

### 4. GitHub secrets

In your repository, go to **Settings → Secrets and variables → Actions → New repository secret** and create these three secrets:

- `TODOIST_API_TOKEN`
- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`

From then on the `.github/workflows/sync.yml` workflow runs every 5 minutes. You can also start it by hand from **Actions → Sync Todoist <-> Notion → Run workflow**, optionally with `dry_run` enabled.

### 5. Running it locally

You need Python 3.12 or later, and a `.env` file with the three values (use `.env.example` as a template).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

set -a; source .env; set +a

python setup_notion_db.py --dry-run   # shows which properties and views are missing
python setup_notion_db.py             # creates them
python sync.py --dry-run              # shows what it would do, without writing anywhere
python sync.py                        # syncs
```

The `.env` file is in `.gitignore`. Never commit it to the repository.

`setup_notion_db.py` creates the missing properties and views, renames the title property to `Name` if it has another name, and adds the P1–P4 options to `Priority`. It never changes the type of an existing property: if one has the wrong type, it reports it so you can fix it by hand. `sync.py` also checks the schema when it starts and stops if it does not match.

`sync.py` options:

| Option           | Effect                                                   |
|------------------|----------------------------------------------------------|
| `--dry-run`      | Logs what it would do without writing to Todoist or Notion |
| `-v`/`--verbose` | Debug logs, including task titles                        |

Without `-v` only the summary is shown, with no task titles, so they never appear in the GitHub Actions logs, which are public in public repositories. To see which tasks would change, use `python sync.py --dry-run -v`.

Exit codes: `0` when everything went fine, `1` when there were sync or API errors, and `2` when configuration is missing.

### 6. Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

### 7. Limits and notes

- **GitHub can delay scheduled runs.** `schedule` runs are not punctual: under heavy load they can be delayed by several minutes or even skipped. Every 5 minutes is a target, not a guarantee, and a change made in Notion takes at least that long to reach Todoist.
- **In public repositories**, GitHub disables scheduled workflows after **60 days without activity** in the repository. To turn it back on, push a commit or re-enable it from the Actions tab.
- Actions minutes are free in public repositories. In private ones every run uses your quota: about 288 runs a day of under a minute each, so check your plan.
- `concurrency` prevents two syncs from running at the same time. If one takes longer than usual, the next one waits in the queue.
- **Recurring tasks:** their due date cannot be changed from Notion, because Todoist would drop the recurrence; if you change it, the Todoist date is restored. Completing one from Notion moves it to its next date, and the page is unchecked again.
- **Deleted tasks:** unchecking `Completed` on the page of a task deleted in Todoist has no effect; the page is checked again on the next run.
- **Formatting:** text is sent to Todoist as plain text, so bold, links and other Notion formatting are lost.
- **Notion limits:** an average of 3 requests per second per integration, text of up to 2000 characters per chunk (long descriptions are split into chunks) and select option names without commas. Commas in project and label names are replaced with spaces.
- **Todoist limits:** 1000 requests per user every 15 minutes. Each run makes very few, because pages hold 200 items.
- **Dates:** dates without a time are stored as all-day dates. "Floating" times use your Todoist account time zone, fixed ones are stored in UTC, and dates with a time set from Notion are sent to Todoist in UTC. Notion shows them in your local time.
- If you delete a page in Notion by hand while its task is still active, it is created again on the next run.
- **Deleting projects from Notion:** to tell a project deleted in Notion from one that is new in Todoist, the projects Notion had on the last run are remembered as short hashes in the description of the `Sync` property. It holds up to 46 projects; beyond that, some projects cannot be deleted from Notion and a warning is logged. The first run after installing only learns the current projects, so nothing is deleted on it.
