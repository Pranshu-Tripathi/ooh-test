import logging

from ooh.config import get_settings
from ooh.db import Database
from ooh.db.models import JobRead, JobStatus, JobType
from ooh.db.repos import GuidanceSourceRepo, JobRepo, RepoSnapshotRepo, RepositoryRepo
from ooh.worker.repository_inspector import LocalRepositoryInspector
from ooh.worker.repository_source_resolver import RepositorySourceResolver

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, db: Database, *, worker_id: str) -> None:
        settings = get_settings()
        self.worker_id = worker_id
        self.job_repo = JobRepo(db)
        self.repository_repo = RepositoryRepo(db)
        self.repo_snapshot_repo = RepoSnapshotRepo(db)
        self.guidance_source_repo = GuidanceSourceRepo(db)
        self.repository_source_resolver = RepositorySourceResolver(cache_root=settings.cache_root)
        self.repository_inspector = LocalRepositoryInspector(cache_root=settings.cache_root)

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
            failed_job = self.job_repo.mark_failed(job.id, error_summary=str(exc))
            if (
                failed_job.status == JobStatus.FAILED
                and failed_job.job_type == JobType.INGEST_REPOSITORY
                and failed_job.repository_id is not None
            ):
                self.repository_repo.mark_failed(failed_job.repository_id)
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
        repository = self.repository_repo.get(job.repository_id)
        if repository is None:
            raise ValueError(f"repository not found: {job.repository_id}")

        resolved_source = self.repository_source_resolver.resolve(repository)
        snapshot = self.repository_inspector.inspect_path(repository, resolved_source.path)
        self.repo_snapshot_repo.create(
            repository_id=job.repository_id,
            commit_sha=snapshot.commit_sha,
            index_uri=snapshot.index_uri,
        )
        self.guidance_source_repo.replace_for_repository(
            job.repository_id,
            [
                (guidance.source_type, guidance.path, guidance.content_hash)
                for guidance in snapshot.guidance_sources
            ],
        )
        self.repository_repo.mark_indexed_at_commit(job.repository_id, commit_sha=snapshot.commit_sha)
        logger.info(
            "repository indexed",
            extra={
                "repository_id": str(job.repository_id),
                "commit_sha": snapshot.commit_sha,
                "file_count": snapshot.file_count,
                "guidance_source_count": len(snapshot.guidance_sources),
            },
        )
