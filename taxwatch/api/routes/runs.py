"""Pipeline run endpoints: trigger, inspect, list."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from taxwatch.db import get_session
from taxwatch.models import JobRun, JobStatus, TriggerType
from taxwatch.services import dashboard as svc

router = APIRouter(prefix="/api/runs", tags=["runs"])


class RunRequest(BaseModel):
    source: str
    stages: list[str] | None = None


class RunResponse(BaseModel):
    job_run_id: int
    status: str


@router.post("", response_model=RunResponse)
def create_run(req: RunRequest, bg: BackgroundTasks) -> RunResponse:
    session = get_session()
    try:
        run = JobRun(
            job_type="pipeline",
            trigger=TriggerType.API,
            source_key=req.source,
            status=JobStatus.RUNNING,
            started_at=datetime.utcnow(),
        )
        session.add(run)
        session.commit()
        run_id = run.id
    finally:
        session.close()

    stop_after = req.stages[-1] if req.stages else None

    def _execute() -> None:
        from taxwatch.jobs.pipeline import execute_pipeline

        s = get_session()
        r = s.get(JobRun, run_id)
        try:
            r.stats = execute_pipeline(s, source_key=req.source, stop_after=stop_after)
            r.status = JobStatus.COMPLETED
        except Exception as exc:  # noqa: BLE001 — recorded on the run for audit
            r.status = JobStatus.FAILED
            r.error = str(exc)
        finally:
            r.finished_at = datetime.utcnow()
            s.commit()
            s.close()

    bg.add_task(_execute)
    return RunResponse(job_run_id=run_id, status="running")


@router.get("")
def list_runs(limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    session = get_session()
    try:
        return {
            "health": svc.get_run_health(session),
            "runs": svc.list_runs(session, limit=limit),
        }
    finally:
        session.close()


@router.get("/{run_id}")
def get_run(run_id: int) -> dict[str, Any]:
    session = get_session()
    try:
        run = session.get(JobRun, run_id)
        if not run:
            raise HTTPException(404, f"Run not found: {run_id}")
        return {
            "id": run.id,
            "job_type": run.job_type,
            "trigger": run.trigger.value,
            "source_key": run.source_key,
            "status": run.status.value,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "stats": run.stats,
            "error": run.error,
        }
    finally:
        session.close()
