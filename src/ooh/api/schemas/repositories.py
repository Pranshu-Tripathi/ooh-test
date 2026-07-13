from datetime import datetime
from pathlib import PurePath
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from ooh.db.models import JobRead, JobStatus, JobType, RepositoryRead, RepositorySourceType, RepositoryStatus


class RepositoryCreateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    source_type: RepositorySourceType
    source_uri: str = Field(min_length=1, max_length=2000)
    default_branch: str | None = Field(default=None, min_length=1, max_length=200)
    token_ref: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("source_uri")
    @classmethod
    def source_uri_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("source_uri must not be blank")
        return stripped


class RepositoryResponse(BaseModel):
    id: UUID
    name: str
    source_type: RepositorySourceType
    source_uri: str
    default_branch: str | None
    status: RepositoryStatus
    last_processed_commit_sha: str | None
    last_indexed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, repository: RepositoryRead) -> "RepositoryResponse":
        return cls(
            id=repository.id,
            name=repository.name,
            source_type=repository.source_type,
            source_uri=repository.source_uri,
            default_branch=repository.default_branch,
            status=repository.status,
            last_processed_commit_sha=repository.last_processed_commit_sha,
            last_indexed_at=repository.last_indexed_at,
            created_at=repository.created_at,
            updated_at=repository.updated_at,
        )


class JobResponse(BaseModel):
    id: UUID
    repository_id: UUID | None
    job_type: JobType
    status: JobStatus
    created_at: datetime

    @classmethod
    def from_record(cls, job: JobRead) -> "JobResponse":
        return cls(
            id=job.id,
            repository_id=job.repository_id,
            job_type=job.job_type,
            status=job.status,
            created_at=job.created_at,
        )


class RepositoryRegistrationResponse(BaseModel):
    repository: RepositoryResponse
    ingest_job: JobResponse


def infer_repository_name(source_uri: str) -> str:
    trimmed = source_uri.rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[:-4]
    name = PurePath(trimmed).name
    return name or "repository"
