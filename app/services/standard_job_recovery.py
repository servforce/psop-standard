from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import StandardProcessingJob


INTERRUPTED_JOB_MESSAGE = "Background job was interrupted by service restart."


def fail_interrupted_workbench_jobs(session: Session) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    counts = {"standard_processing_jobs": _fail_standard_processing_jobs(session, now)}
    session.commit()
    return counts


def _fail_standard_processing_jobs(session: Session, now: datetime) -> int:
    jobs = session.scalars(select(StandardProcessingJob).where(StandardProcessingJob.status == "running")).all()
    for job in jobs:
        job.status = "failed"
        job.stage = "failed"
        job.progress_percent = 100
        job.error_message = INTERRUPTED_JOB_MESSAGE
        job.completed_at = now
        job.updated_at = now
        session.add(job)
    return len(jobs)
