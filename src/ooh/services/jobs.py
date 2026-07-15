from uuid import UUID

from ooh.db.models import JobRead
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

    def claim_next(self, *, worker_id: str) -> JobRead | None:
        return self.job_repo.claim_next(worker_id=worker_id)

    def mark_succeeded(self, job_id: UUID) -> JobRead:
        return self.job_repo.mark_succeeded(job_id)

    def mark_failed(self, job_id: UUID, *, error_summary: str, retry: bool = True) -> JobRead:
        return self.job_repo.mark_failed(job_id, error_summary=error_summary, retry=retry)
