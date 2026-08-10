"""JSON-only FastAPI app.

Every endpoint lives in a router under `taxwatch.api.routes` so the web
dashboard (`taxwatch.web.app`) can serve the identical API surface from a
single process.
"""
from __future__ import annotations

from fastapi import FastAPI

from taxwatch.api.routes import ALL_ROUTERS

app = FastAPI(title="TaxWatch API", version="0.1.0")

for router in ALL_ROUTERS:
    app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
