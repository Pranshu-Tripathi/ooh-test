from uuid import UUID

from sqlalchemy import select

from ooh.db.connection import Database
from ooh.db.models import Job, JobRead, JobType, Repository, RepositoryRead, RepositorySourceType


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
