from typing import Any
from uuid import UUID

from ooh.db.connection import Database
from ooh.db.models import Job, JobRead, JobType


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
