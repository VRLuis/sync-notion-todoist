"""Configuration read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(Exception):
    pass


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing environment variable {name}")
    return value


@dataclass(frozen=True)
class Config:
    todoist_token: str
    notion_token: str
    notion_database_id: str

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            todoist_token=require_env("TODOIST_API_TOKEN"),
            notion_token=require_env("NOTION_TOKEN"),
            notion_database_id=require_env("NOTION_DATABASE_ID"),
        )
