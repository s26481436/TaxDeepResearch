"""FastAPI app for manual triggering and querying."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from taxwatch.db import get_session
from taxwatch.graph.relations import get_entity_context, get_impact_spread
from taxwatch.models import Change, Document, JobRun, JobStatus, Source

app = FastAPI(title="TaxWatch API", version="0.1.0")


class RunRequest(BaseModel):
    source: str
    stages: list[str] | None = None


class RunResponse(BaseModel):
    job_run_id: int
    status: str


@app.post("/api/runs", response_model=RunResponse)
def create_run(req: RunRequest, bg: BackgroundTasks):

    session = get_session()
    run = JobRun(
        job_type="pipeline",
        trigger="api",
        source_key=req.source,
        status=JobStatus.RUNNING,
        started_at=datetime.utcnow(),
    )
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    stop_after = req.stages[-1] if req.stages else None

    def _execute():
        from taxwatch.jobs.pipeline import execute_pipeline

        s = get_session()
        r = s.get(JobRun, run_id)
        try:
            stats = execute_pipeline(s, source_key=req.source, stop_after=stop_after)
            r.status = JobStatus.COMPLETED
            r.stats = stats
        except Exception as exc:
            r.status = JobStatus.FAILED
            r.error = str(exc)
        finally:
            r.finished_at = datetime.utcnow()
            s.commit()
            s.close()

    bg.add_task(_execute)
    return RunResponse(job_run_id=run_id, status="running")


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    session = get_session()
    try:
        run = session.get(JobRun, run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        return {
            "id": run.id,
            "job_type": run.job_type,
            "source_key": run.source_key,
            "status": run.status.value,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "stats": run.stats,
            "error": run.error,
        }
    finally:
        session.close()


@app.get("/api/changes")
def list_changes(days: int = 7, country: str | None = None, limit: int = 50):
    session = get_session()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days)
        q = session.query(Change).filter(Change.detected_at >= cutoff)
        if country:
            q = (
                q.join(Document, Change.document_id == Document.id)
                .join(Source, Document.source_id == Source.id)
                .filter(Source.country == country)
            )
        changes = q.order_by(Change.detected_at.desc()).limit(limit).all()
        return [
            {
                "id": c.id,
                "node_key": c.node_key,
                "change_type": c.change_type.value,
                "severity": c.severity.value,
                "detected_at": c.detected_at.isoformat(),
            }
            for c in changes
        ]
    finally:
        session.close()


@app.get("/api/entities/{entity_key}/context")
def entity_context(entity_key: str):
    session = get_session()
    try:
        ctx = get_entity_context(session, entity_key)
        if not ctx:
            raise HTTPException(404, "Entity not found")
        return {
            "entity": {"key": ctx["entity"].entity_key, "title": ctx["entity"].canonical_title},
            "parent_laws": [
                {"relation": r.relation_type.value, "key": e.entity_key, "title": e.canonical_title}
                for r, e in ctx["parent_laws"]
            ],
            "children": [
                {"relation": r.relation_type.value, "key": e.entity_key, "title": e.canonical_title}
                for r, e in ctx["children"]
            ],
            "siblings": [
                {"key": e.entity_key, "title": e.canonical_title}
                for e in ctx["siblings"]
            ],
        }
    finally:
        session.close()


@app.get("/api/entities/{entity_key}/impact")
def entity_impact(entity_key: str, max_depth: int = 3):
    session = get_session()
    try:
        entities = get_impact_spread(session, entity_key, max_depth)
        return [
            {"key": e.entity_key, "title": e.canonical_title, "type": e.entity_type.value}
            for e in entities
        ]
    finally:
        session.close()
