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
    llm_max_tokens: int = 16384
    # A single 60k-char extraction request was measured at 6m41s against the
    # production gateway. The old 120s default timed out every batch, which
    # surfaced as "the LLM is unreachable".
    llm_timeout: int = 900
    # The gateway in front of this deployment answers overload with 400 rather
    # than 429, and a batched extraction fires eight to ten calls in a row —
    # one transient refusal used to kill the whole run.
    llm_retry_attempts: int = 5
    llm_retry_base_delay: float = 2.0
    llm_retry_on_bad_request: bool = True
    # Seconds to wait between batches. The gateway returns 400 when several
    # requests land together, so a batched run paces itself rather than
    # firing every batch back to back.
    llm_inter_batch_delay: float = 1.0

    # Chars of provision text per extraction batch. Deliberately well under
    # the 60k hard cap: smaller requests finish sooner and are far less
    # likely to be caught by a transient gateway failure.
    requirements_batch_chars: int = 20_000

    # 国家税务总局 policy-library search — the primary source of external
    # corroboration for CN documents. Same backend the fgk site itself queries,
    # so it costs no API quota and returns official documents (with their
    # 时效性), which a third-party search engine cannot supply.
    fgk_search_enabled: bool = True
    fgk_search_max_results: int = 5
    fgk_search_timeout: int = 20

    # Brave Search — fallback corroboration when neither the local corpus nor
    # the official library has anything. Metered, so it is rate-limited,
    # cached, and capped per run.
    brave_search_api_key: str = ""
    brave_search_enabled: bool = True
    brave_search_max_results: int = 5
    brave_search_timeout: int = 10
    # The free tier allows 2,000 queries a month at one per second. A cap per
    # run is what keeps a single large crawl from spending the month's budget.
    brave_search_max_queries_per_run: int = 30
    brave_search_min_interval: float = 1.1
    brave_search_cache_ttl_days: int = 30
    # Empty means every non-cosmetic change may reach Brave. Set to "major" to
    # spend quota only on the changes worth corroborating.
    brave_search_min_severity: str = ""

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
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("sources", {})
