"""JSON-only FastAPI app.

Every endpoint lives in a router under `taxwatch.api.routes` so the web
dashboard (`taxwatch.web.app`) can serve the identical API surface from a
single process. Both apps share `lifespan` too, so this one cannot serve those
routes against a database that was never initialised.
"""

from __future__ import annotations

from fastapi import FastAPI

from taxwatch.api.lifespan import lifespan
from taxwatch.api.routes import ALL_ROUTERS

app = FastAPI(title="TaxWatch API", version="0.1.0", lifespan=lifespan)

for router in ALL_ROUTERS:
    app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
