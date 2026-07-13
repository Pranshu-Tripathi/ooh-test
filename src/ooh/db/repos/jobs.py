from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from ooh.db.connection import Database
from ooh.db.models import Job, JobRead, JobStatus, JobType


class JobRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue(
        self,
        *,
        job_type: JobType,
        repository_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> JobRead:
        with self.db.session() as session:
            job = Job(
                repository_id=repository_id,
                job_type=job_type,
                payload=payload or {},
                max_attempts=max_attempts,
            )
            session.add(job)
            session.flush()
            session.refresh(job)
            return JobRead.model_validate(job)

    def claim_next(self, *, worker_id: str) -> JobRead | None:
        now = datetime.now(UTC)

        with self.db.session() as session:
            job = session.scalar(
                select(Job)
                .where(
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RETRY_WAIT]),
                    Job.run_after <= now,
                )
                .order_by(Job.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None

            job.status = JobStatus.RUNNING
            job.locked_by = worker_id
            job.locked_at = now
            job.attempt_count += 1
            job.error_summary = None
            job.updated_at = now
            session.flush()
            session.refresh(job)
            return JobRead.model_validate(job)

    def mark_succeeded(self, job_id: UUID) -> JobRead:
        now = datetime.now(UTC)

        with self.db.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError(f"job not found: {job_id}")

            job.status = JobStatus.SUCCEEDED
            job.locked_by = None
            job.locked_at = None
            job.error_summary = None
            job.updated_at = now
            session.flush()
            session.refresh(job)
            return JobRead.model_validate(job)

    def mark_failed(self, job_id: UUID, *, error_summary: str, retry: bool = True) -> JobRead:
        now = datetime.now(UTC)

        with self.db.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError(f"job not found: {job_id}")

            should_retry = retry and job.attempt_count < job.max_attempts
            job.status = JobStatus.RETRY_WAIT if should_retry else JobStatus.FAILED
            job.run_after = now + self._retry_delay(job.attempt_count) if should_retry else job.run_after
            job.locked_by = None
            job.locked_at = None
            job.error_summary = error_summary[:1000]
            job.updated_at = now
            session.flush()
            session.refresh(job)
            return JobRead.model_validate(job)

    @staticmethod
    def _retry_delay(attempt_count: int) -> timedelta:
        return timedelta(seconds=min(300, 2**attempt_count))
