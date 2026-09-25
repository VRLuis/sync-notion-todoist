#!/usr/bin/env python3
"""Two-way sync between Todoist and a Notion database.

Each active Todoist task is kept as a page in the Notion database. Edits made
on either side reach the other one, and when both sides edit the same field
Todoist wins. Nothing is ever deleted: pages whose tasks are no longer active
are marked as completed.
"""

from __future__ import annotations

import argparse
import logging
import sys

from scripts.config import Config, ConfigError
from scripts.sync import run

log = logging.getLogger("sync")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Todoist tasks with Notion.")
    parser.add_argument(
        "--dry-run", action="store_true", help="show the changes without writing to Todoist or Notion"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logs, including task titles")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        stats = run(Config.from_env(), args.dry_run)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    except Exception:
        log.exception("The sync failed")
        return 1

    log.info("%sSummary: %s", "[dry-run] " if args.dry_run else "", stats.summary())
    for error in stats.errors:
        log.error("  - %s", error)
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
