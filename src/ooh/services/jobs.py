from datetime import timedelta
from typing import Any
from uuid import UUID

from ooh.db.models import JobRead, JobType
from ooh.db.repos import JobRepo
from ooh.services.exceptions import NotFoundError


class JobService:
    def __init__(self, *, job_repo: JobRepo) -> None:
        self.job_repo = job_repo

    def get_job(self, job_id: UUID) -> JobRead:
        job = self.job_repo.get(job_id)
        if job is None:
            raise NotFoundError("job not found")
        return job

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
        job_types: set[JobType] | None = None,
    ) -> JobRead | None:
        return self.job_repo.claim_next(
            worker_id=worker_id,
            lease_duration=lease_duration,
            job_types=job_types,
        )

    def mark_succeeded(
        self,
        job_id: UUID,
        *,
        result_metadata: dict[str, Any] | None = None,
        expected_worker_id: str | None = None,
        expected_attempt_count: int | None = None,
    ) -> JobRead:
        return self.job_repo.mark_succeeded(
            job_id,
            result_metadata=result_metadata,
            expected_worker_id=expected_worker_id,
            expected_attempt_count=expected_attempt_count,
        )

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
        return self.job_repo.mark_failed(
            job_id,
            error_summary=error_summary,
            retry=retry,
            result_metadata=result_metadata,
            expected_worker_id=expected_worker_id,
            expected_attempt_count=expected_attempt_count,
        )
