from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from ooh.db.models import JobRead, JobStatus, JobType


class JobDetailResponse(BaseModel):
    id: UUID
    repository_id: UUID | None
    job_type: JobType
    status: JobStatus
    attempt_count: int
    max_attempts: int
    payload: dict[str, Any]
    result_metadata: dict[str, Any]
    run_after: datetime
    locked_by: str | None
    locked_at: datetime | None
    lease_expires_at: datetime | None
    idempotency_key: str | None
    error_summary: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, job: JobRead) -> "JobDetailResponse":
        return cls(
            id=job.id,
            repository_id=job.repository_id,
            job_type=job.job_type,
            status=job.status,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            payload=job.payload,
            result_metadata=job.result_metadata,
            run_after=job.run_after,
            locked_by=job.locked_by,
            locked_at=job.locked_at,
            lease_expires_at=job.lease_expires_at,
            idempotency_key=job.idempotency_key,
            error_summary=job.error_summary,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
