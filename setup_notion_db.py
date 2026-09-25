#!/usr/bin/env python3
"""Check that the Notion database has the properties sync.py uses and create
the missing ones, along with the views defined in scripts/views.py.

It never changes the type of an existing property (that could lose data): if
one has a different type, it reports it and exits with an error so you can
fix it by hand.
"""

from __future__ import annotations

import argparse
import logging
import sys

from scripts.config import ConfigError, require_env
from scripts.notion import NotionClient
from scripts.schema import plan_schema_changes, schema_problems
from scripts.views import desired_views

log = logging.getLogger("setup")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the Notion database.")
    parser.add_argument("--dry-run", action="store_true", help="only show what would change")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    try:
        notion = NotionClient(require_env("NOTION_TOKEN"), require_env("NOTION_DATABASE_ID"))
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    try:
        changes, errors = plan_schema_changes(notion.get_schema())
        
        for name, change in changes.items():

            if "name" in change:
                log.info("Rename '%s' → '%s'", name, change["name"])
            else:
                log.info("Create '%s' (%s)", name, next(iter(change)))

        if changes and not args.dry_run:

            notion.update_schema(changes)
            log.info("Database updated (%d changes)", len(changes))
            
        elif changes:
            log.info("[dry-run] %d pending changes", len(changes))
            
        else:
            log.info("No properties to create")

    except Exception as exc:

        log.error("Could not prepare the database: %s", exc)
        return 1

    for error in errors:
        log.error("%s", error)
    if errors:
        log.error("Fix the properties with a wrong type by hand and run again")

        return 1

    try:
        create_missing_views(notion, args.dry_run)
        
    except Exception as exc:
        log.error("Could not create the views: %s", exc)

        return 1
    return 0


def create_missing_views(notion: NotionClient, dry_run: bool) -> None:
    """Create the missing views, in order. Existing views are left untouched."""
    schema = notion.get_schema()
    
    if schema_problems(schema):
        log.info("[dry-run] Views will be checked once all properties exist")

        return
    
    existing = notion.get_view_ids_by_name()
    previous_id = None

    for view in desired_views(schema):

        if view["name"] in existing:
            previous_id = existing[view["name"]]

            continue

        log.info("%sCreate view '%s'", "[dry-run] " if dry_run else "", view["name"])

        if not dry_run:
            
            previous_id = notion.create_view(view, previous_id)


if __name__ == "__main__":
    sys.exit(main())
