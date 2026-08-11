"""Job runner abstraction — LocalRunner now, CeleryRunner later."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from taxwatch.db import get_session
from taxwatch.models import JobRun, JobStatus, TriggerType


class JobRunner(ABC):
    @abstractmethod
    def submit(
        self,
        job_type: str,
        source_key: str = "",
        trigger: TriggerType = TriggerType.MANUAL,
        **kwargs,
    ) -> JobRun: ...


class LocalRunner(JobRunner):
    """Synchronous runner — executes immediately in the current process."""

    def submit(
        self,
        job_type: str,
        source_key: str = "",
        trigger: TriggerType = TriggerType.MANUAL,
        **kwargs,
    ) -> JobRun:
        session = get_session()
        run = JobRun(
            job_type=job_type,
            trigger=trigger,
            source_key=source_key,
            status=JobStatus.RUNNING,
            started_at=datetime.utcnow(),
        )
        session.add(run)
        session.commit()

        try:
            from taxwatch.jobs.pipeline import execute_pipeline

            stats = execute_pipeline(session, source_key=source_key, **kwargs)
            run.status = JobStatus.COMPLETED
            run.stats = stats
        except Exception as exc:
            run.status = JobStatus.FAILED
            run.error = str(exc)
            raise
        finally:
            run.finished_at = datetime.utcnow()
            session.commit()
            session.close()

        return run
