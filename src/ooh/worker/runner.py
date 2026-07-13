import logging

from ooh.db import Database
from ooh.db.models import JobRead, JobType
from ooh.db.repos import JobRepo, RepositoryRepo

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, db: Database, *, worker_id: str) -> None:
        self.worker_id = worker_id
        self.job_repo = JobRepo(db)
        self.repository_repo = RepositoryRepo(db)

    def process_once(self) -> bool:
        job = self.job_repo.claim_next(worker_id=self.worker_id)
        if job is None:
            return False

        logger.info(
            "claimed job",
            extra={"job_id": str(job.id), "job_type": job.job_type.value, "worker_id": self.worker_id},
        )

        try:
            self.dispatch(job)
        except Exception as exc:
            logger.exception("job failed", extra={"job_id": str(job.id), "job_type": job.job_type.value})
            self.job_repo.mark_failed(job.id, error_summary=str(exc))
            return True

        self.job_repo.mark_succeeded(job.id)
        logger.info("job succeeded", extra={"job_id": str(job.id), "job_type": job.job_type.value})
        return True

    def dispatch(self, job: JobRead) -> None:
        if job.job_type == JobType.INGEST_REPOSITORY:
            self.ingest_repository(job)
            return

        raise ValueError(f"unsupported job type: {job.job_type.value}")

    def ingest_repository(self, job: JobRead) -> None:
        if job.repository_id is None:
            raise ValueError("ingest_repository job requires repository_id")

        self.repository_repo.mark_indexing(job.repository_id)
        # Real repository cloning/indexing lands in the next ingestion slice.
        self.repository_repo.mark_indexed(job.repository_id)
