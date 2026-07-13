from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ooh.db.models import Job, JobRead, Repository, RepositoryRead


class RepositoryDao:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        name: str,
        source_type: str,
        source_uri: str,
        default_branch: str | None = None,
        token_ref: str | None = None,
    ) -> RepositoryRead:
        repository = Repository(
            name=name,
            source_type=source_type,
            source_uri=source_uri,
            default_branch=default_branch,
            token_ref=token_ref,
        )
        self.session.add(repository)
        self.session.flush()
        self.session.refresh(repository)
        return RepositoryRead.model_validate(repository)

    def get(self, repository_id: UUID) -> RepositoryRead | None:
        repository = self.session.scalar(select(Repository).where(Repository.id == repository_id))
        if repository is None:
            return None
        return RepositoryRead.model_validate(repository)

    def list_all(self) -> list[RepositoryRead]:
        repositories = self.session.scalars(select(Repository).order_by(Repository.created_at.desc())).all()
        return [RepositoryRead.model_validate(repository) for repository in repositories]


class JobDao:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self,
        *,
        job_type: str,
        repository_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> JobRead:
        job = Job(
            repository_id=repository_id,
            job_type=job_type,
            payload=payload or {},
            max_attempts=max_attempts,
        )
        self.session.add(job)
        self.session.flush()
        self.session.refresh(job)
        return JobRead.model_validate(job)
