from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from ooh.db.connection import Database
from ooh.db.models import (
    Job,
    JobRead,
    JobType,
    Repository,
    RepositoryRead,
    RepositorySourceType,
    RepositoryStatus,
)


class RepositoryRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        *,
        name: str,
        source_type: RepositorySourceType,
        source_uri: str,
        default_branch: str | None = None,
        token_ref: str | None = None,
    ) -> RepositoryRead:
        with self.db.session() as session:
            repository = Repository(
                name=name,
                source_type=source_type,
                source_uri=source_uri,
                default_branch=default_branch,
                token_ref=token_ref,
            )
            session.add(repository)
            session.flush()
            session.refresh(repository)
            return RepositoryRead.model_validate(repository)

    def register_with_ingest_job(
        self,
        *,
        name: str,
        source_type: RepositorySourceType,
        source_uri: str,
        default_branch: str | None = None,
        token_ref: str | None = None,
    ) -> tuple[RepositoryRead, JobRead]:
        with self.db.session() as session:
            repository = Repository(
                name=name,
                source_type=source_type,
                source_uri=source_uri,
                default_branch=default_branch,
                token_ref=token_ref,
            )
            session.add(repository)
            session.flush()
            session.refresh(repository)

            job = Job(
                repository_id=repository.id,
                job_type=JobType.INGEST_REPOSITORY,
                payload={
                    "repository_id": str(repository.id),
                    "source_type": repository.source_type.value,
                    "source_uri": repository.source_uri,
                },
            )
            session.add(job)
            session.flush()
            session.refresh(job)

            return RepositoryRead.model_validate(repository), JobRead.model_validate(job)

    def get(self, repository_id: UUID) -> RepositoryRead | None:
        with self.db.session() as session:
            repository = session.scalar(select(Repository).where(Repository.id == repository_id))
            if repository is None:
                return None
            return RepositoryRead.model_validate(repository)

    def list_all(self) -> list[RepositoryRead]:
        with self.db.session() as session:
            repositories = session.scalars(select(Repository).order_by(Repository.created_at.desc())).all()
            return [RepositoryRead.model_validate(repository) for repository in repositories]

    def mark_indexing(self, repository_id: UUID) -> RepositoryRead:
        return self.update_status(repository_id, RepositoryStatus.INDEXING)

    def mark_indexed(self, repository_id: UUID) -> RepositoryRead:
        return self.update_status(repository_id, RepositoryStatus.INDEXED, last_indexed_at=datetime.now(UTC))

    def mark_indexed_at_commit(self, repository_id: UUID, *, commit_sha: str) -> RepositoryRead:
        return self.update_status(
            repository_id,
            RepositoryStatus.INDEXED,
            last_indexed_at=datetime.now(UTC),
            last_processed_commit_sha=commit_sha,
        )

    def mark_failed(self, repository_id: UUID) -> RepositoryRead:
        return self.update_status(repository_id, RepositoryStatus.FAILED)

    def update_status(
        self,
        repository_id: UUID,
        status: RepositoryStatus,
        *,
        last_indexed_at: datetime | None = None,
        last_processed_commit_sha: str | None = None,
    ) -> RepositoryRead:
        now = datetime.now(UTC)

        with self.db.session() as session:
            repository = session.get(Repository, repository_id)
            if repository is None:
                raise ValueError(f"repository not found: {repository_id}")

            repository.status = status
            repository.updated_at = now
            if last_indexed_at is not None:
                repository.last_indexed_at = last_indexed_at
            if last_processed_commit_sha is not None:
                repository.last_processed_commit_sha = last_processed_commit_sha
            session.flush()
            session.refresh(repository)
            return RepositoryRead.model_validate(repository)
