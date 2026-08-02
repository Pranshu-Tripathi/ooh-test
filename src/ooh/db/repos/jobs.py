from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from ooh.db.connection import Database
from ooh.db.models import ExecutionEventType, Job, JobRead, JobStatus, JobType
from ooh.db.repos.execution_events import ExecutionEventInput, append_execution_event


class JobRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, job_id: UUID) -> JobRead | None:
        with self.db.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                return None
            return JobRead.model_validate(job)

    def enqueue(
        self,
        *,
        job_type: JobType,
        repository_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
        idempotency_key: str | None = None,
    ) -> JobRead:
        with self.db.session() as session:
            return enqueue_job(
                session,
                repository_id=repository_id,
                job_type=job_type,
                payload=payload or {},
                max_attempts=max_attempts,
                idempotency_key=idempotency_key,
            )

    def has_active_repository_job(
        self,
        *,
        repository_id: UUID,
        job_type: JobType,
    ) -> bool:
        with self.db.session() as session:
            job_id = session.scalar(
                select(Job.id)
                .where(
                    Job.repository_id == repository_id,
                    Job.job_type == job_type,
                    Job.status.in_(
                        [
                            JobStatus.QUEUED,
                            JobStatus.RUNNING,
                            JobStatus.RETRY_WAIT,
                        ]
                    ),
                )
                .limit(1)
            )
            return job_id is not None

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
        job_types: set[JobType] | None = None,
    ) -> JobRead | None:
        now = datetime.now(UTC)

        with self.db.session() as session:
            self._recover_expired_leases(session, now=now)
            conditions = [
                Job.status.in_([JobStatus.QUEUED, JobStatus.RETRY_WAIT]),
                Job.run_after <= now,
                Job.attempt_count < Job.max_attempts,
            ]
            if job_types is not None:
                if not job_types:
                    return None
                conditions.append(Job.job_type.in_(job_types))
            job = session.scalar(
                select(Job)
                .where(*conditions)
                .order_by(Job.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None

            job.status = JobStatus.RUNNING
            job.locked_by = worker_id
            job.locked_at = now
            job.lease_expires_at = now + lease_duration
            job.attempt_count += 1
            job.error_summary = None
            job.result_metadata = {}
            job.updated_at = now
            session.flush()
            session.refresh(job)
            append_execution_event(
                session,
                ExecutionEventInput(
                    event_type=ExecutionEventType.JOB_STATUS_CHANGED,
                    repository_id=job.repository_id,
                    job_id=job.id,
                    payload={
                        "status": JobStatus.RUNNING.value,
                        "attempt_count": job.attempt_count,
                        "worker_id": worker_id,
                        "lease_expires_at": job.lease_expires_at.isoformat(),
                    },
                ),
            )
            return JobRead.model_validate(job)

    def mark_succeeded(
        self,
        job_id: UUID,
        *,
        result_metadata: dict[str, Any] | None = None,
        expected_worker_id: str | None = None,
        expected_attempt_count: int | None = None,
    ) -> JobRead:
        now = datetime.now(UTC)

        with self.db.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError(f"job not found: {job_id}")
            self._validate_lease_owner(
                job,
                expected_worker_id=expected_worker_id,
                expected_attempt_count=expected_attempt_count,
            )

            job.status = JobStatus.SUCCEEDED
            job.locked_by = None
            job.locked_at = None
            job.lease_expires_at = None
            job.error_summary = None
            job.result_metadata = result_metadata or {}
            job.updated_at = now
            session.flush()
            session.refresh(job)
            append_execution_event(
                session,
                ExecutionEventInput(
                    event_type=ExecutionEventType.JOB_STATUS_CHANGED,
                    repository_id=job.repository_id,
                    job_id=job.id,
                    payload={
                        "status": JobStatus.SUCCEEDED.value,
                        "attempt_count": job.attempt_count,
                        "result_metadata": job.result_metadata,
                    },
                ),
            )
            return JobRead.model_validate(job)

    def mark_failed(
        self,
        job_id: UUID,
        *,
        error_summary: str,
        retry: bool = True,
        result_metadata: dict[str, Any] | None = None,
        expected_worker_id: str | None = None,
        expected_attempt_count: int | None = None,
    ) -> JobRead:
        now = datetime.now(UTC)

        with self.db.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError(f"job not found: {job_id}")
            self._validate_lease_owner(
                job,
                expected_worker_id=expected_worker_id,
                expected_attempt_count=expected_attempt_count,
            )

            should_retry = retry and job.attempt_count < job.max_attempts
            job.status = JobStatus.RETRY_WAIT if should_retry else JobStatus.FAILED
            job.run_after = now + self._retry_delay(job.attempt_count) if should_retry else job.run_after
            job.locked_by = None
            job.locked_at = None
            job.lease_expires_at = None
            job.error_summary = error_summary[:1000]
            job.result_metadata = result_metadata or {}
            job.updated_at = now
            session.flush()
            session.refresh(job)
            append_execution_event(
                session,
                ExecutionEventInput(
                    event_type=ExecutionEventType.JOB_STATUS_CHANGED,
                    repository_id=job.repository_id,
                    job_id=job.id,
                    payload={
                        "status": job.status.value,
                        "attempt_count": job.attempt_count,
                        "run_after": job.run_after.isoformat(),
                        "error_summary": job.error_summary,
                    },
                ),
            )
            return JobRead.model_validate(job)

    @staticmethod
    def _recover_expired_leases(session: Session, *, now: datetime, limit: int = 100) -> None:
        expired_jobs = session.scalars(
            select(Job)
            .where(
                Job.status == JobStatus.RUNNING,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at <= now,
            )
            .order_by(Job.lease_expires_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        ).all()
        for job in expired_jobs:
            exhausted = job.attempt_count >= job.max_attempts
            job.status = JobStatus.FAILED if exhausted else JobStatus.QUEUED
            job.run_after = now
            job.locked_by = None
            job.locked_at = None
            job.lease_expires_at = None
            job.error_summary = (
                "Job stopped after its worker lease expired."
                if exhausted
                else "Previous worker lease expired; job queued for recovery."
            )
            job.updated_at = now
            session.flush()
            append_execution_event(
                session,
                ExecutionEventInput(
                    event_type=ExecutionEventType.JOB_STATUS_CHANGED,
                    repository_id=job.repository_id,
                    job_id=job.id,
                    payload={
                        "status": job.status.value,
                        "attempt_count": job.attempt_count,
                        "reason": "lease_expired",
                    },
                ),
            )

    @staticmethod
    def _validate_lease_owner(
        job: Job,
        *,
        expected_worker_id: str | None,
        expected_attempt_count: int | None,
    ) -> None:
        if job.status != JobStatus.RUNNING:
            raise RuntimeError(f"job is no longer running: {job.id}")
        if expected_worker_id is not None and job.locked_by != expected_worker_id:
            raise RuntimeError(f"job lease owner changed: {job.id}")
        if expected_attempt_count is not None and job.attempt_count != expected_attempt_count:
            raise RuntimeError(f"job lease attempt changed: {job.id}")

    @staticmethod
    def _retry_delay(attempt_count: int) -> timedelta:
        return timedelta(seconds=min(300, 2**attempt_count))


def enqueue_job(
    session: Session,
    *,
    job_type: JobType,
    repository_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
    idempotency_key: str | None = None,
) -> JobRead:
    """Create a durable job, returning the existing job for a repeated idempotency key."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise ValueError("idempotency_key must not be blank")
        statement = (
            postgresql_insert(Job)
            .values(
                repository_id=repository_id,
                job_type=job_type,
                payload=payload or {},
                max_attempts=max_attempts,
                idempotency_key=idempotency_key,
            )
            .on_conflict_do_nothing(index_elements=[Job.idempotency_key])
            .returning(Job.id)
        )
        job_id = session.scalar(statement)
        if job_id is None:
            existing = session.scalar(
                select(Job).where(Job.idempotency_key == idempotency_key)
            )
            if existing is None:
                raise RuntimeError("idempotent job insert did not return an existing job")
            return JobRead.model_validate(existing)
        job = session.get(Job, job_id)
        if job is None:
            raise RuntimeError(f"created job could not be loaded: {job_id}")
    else:
        job = Job(
            repository_id=repository_id,
            job_type=job_type,
            payload=payload or {},
            max_attempts=max_attempts,
        )
        session.add(job)
        session.flush()
        session.refresh(job)

    append_execution_event(
        session,
        ExecutionEventInput(
            event_type=ExecutionEventType.JOB_STATUS_CHANGED,
            repository_id=job.repository_id,
            job_id=job.id,
            payload={
                "status": JobStatus.QUEUED.value,
                "job_type": job.job_type.value,
                "idempotency_key": job.idempotency_key,
            },
        ),
    )
    return JobRead.model_validate(job)
