from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://taxwatch:taxwatch_dev@localhost:5432/taxwatch"
    db_schema: str = ""  # PostgreSQL schema name; empty = use public (default)

    llm_base_url: str = "http://localhost:8000/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "default"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    llm_timeout: int = 120

    # Brave Search — external corroboration during change analysis.
    brave_search_api_key: str = ""
    brave_search_enabled: bool = True
    brave_search_max_results: int = 5
    brave_search_timeout: int = 10

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = "taxwatch@example.com"
    email_to: str = ""

    data_dir: Path = Field(default=Path("./data"))
    sources_path: Path = Field(default=Path("config/sources.yaml"))


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def load_sources(path: Path | None = None) -> dict[str, Any]:
    p = path or get_settings().sources_path
    with open(p) as f:
        data = yaml.safe_load(f)
    return data.get("sources", {})
