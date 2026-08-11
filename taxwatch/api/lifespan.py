"""Shared application lifecycle for every FastAPI entry point.

`ALL_ROUTERS` already guarantees the JSON app and the dashboard expose the same
endpoints. That is only half the contract: an endpoint that exists but queries a
database nobody initialised fails with `relation "..." does not exist`, which is
exactly what happened when `taxwatch.api.main` shipped without the startup hook
`taxwatch.web.app` had. Sharing the lifespan the same way the routers are shared
means a new entry point cannot inherit the routes while missing the setup.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Create any missing tables and columns before serving the first request."""
    from taxwatch.db import init_db

    init_db()
    logger.info("Database schema ready")
    yield
