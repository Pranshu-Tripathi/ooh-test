from datetime import datetime
from decimal import Decimal
from pathlib import PurePath
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from ooh.db.models import (
    AttentionFocusAreaRead,
    AttentionProfileRead,
    ContextPackRead,
    ContextPackSourceRead,
    ContextPackSourceType,
    ContextPackType,
    DriftEventRead,
    DriftSeverity,
    JobRead,
    JobStatus,
    JobType,
    RepositoryRead,
    RepositorySourceType,
    RepositoryStatus,
)


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


class DriftEventResponse(BaseModel):
    id: UUID
    repository_id: UUID
    snapshot_id: UUID | None
    from_commit_sha: str | None
    to_commit_sha: str
    drift_score: Decimal
    severity: DriftSeverity
    breakdown: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_record(cls, drift_event: DriftEventRead) -> "DriftEventResponse":
        return cls(
            id=drift_event.id,
            repository_id=drift_event.repository_id,
            snapshot_id=drift_event.snapshot_id,
            from_commit_sha=drift_event.from_commit_sha,
            to_commit_sha=drift_event.to_commit_sha,
            drift_score=drift_event.drift_score,
            severity=drift_event.severity,
            breakdown=drift_event.breakdown,
            created_at=drift_event.created_at,
        )


class AttentionFocusAreaRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    weight: Decimal = Field(default=Decimal("1.0"), ge=Decimal("0"), le=Decimal("10"))
    path_globs: list[str] = Field(min_length=1, max_length=50)

    @field_validator("path_globs")
    @classmethod
    def path_globs_must_not_be_blank(cls, value: list[str]) -> list[str]:
        stripped = [path_glob.strip() for path_glob in value if path_glob.strip()]
        if not stripped:
            raise ValueError("path_globs must include at least one non-blank glob")
        return stripped


class AttentionProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    default_weight: Decimal = Field(default=Decimal("1.0"), ge=Decimal("0"), le=Decimal("10"))
    active: bool = True
    focus_areas: list[AttentionFocusAreaRequest] = Field(default_factory=list, max_length=50)


class AttentionFocusAreaResponse(BaseModel):
    id: UUID
    attention_profile_id: UUID
    name: str
    description: str | None
    weight: Decimal
    path_globs: list[str]
    created_at: datetime

    @classmethod
    def from_record(cls, focus_area: AttentionFocusAreaRead) -> "AttentionFocusAreaResponse":
        return cls(
            id=focus_area.id,
            attention_profile_id=focus_area.attention_profile_id,
            name=focus_area.name,
            description=focus_area.description,
            weight=focus_area.weight,
            path_globs=focus_area.path_globs,
            created_at=focus_area.created_at,
        )


class AttentionProfileResponse(BaseModel):
    id: UUID
    repository_id: UUID
    name: str
    default_weight: Decimal
    active: bool
    focus_areas: list[AttentionFocusAreaResponse]
    created_at: datetime

    @classmethod
    def from_records(
        cls,
        profile: AttentionProfileRead,
        focus_areas: list[AttentionFocusAreaRead],
    ) -> "AttentionProfileResponse":
        return cls(
            id=profile.id,
            repository_id=profile.repository_id,
            name=profile.name,
            default_weight=profile.default_weight,
            active=profile.active,
            focus_areas=[
                AttentionFocusAreaResponse.from_record(focus_area) for focus_area in focus_areas
            ],
            created_at=profile.created_at,
        )


class ContextPackSourceResponse(BaseModel):
    id: UUID
    context_pack_id: UUID
    source_type: ContextPackSourceType
    source_uri: str
    content_hash: str | None
    created_at: datetime

    @classmethod
    def from_record(cls, source: ContextPackSourceRead) -> "ContextPackSourceResponse":
        return cls(
            id=source.id,
            context_pack_id=source.context_pack_id,
            source_type=source.source_type,
            source_uri=source.source_uri,
            content_hash=source.content_hash,
            created_at=source.created_at,
        )


class ContextPackResponse(BaseModel):
    id: UUID
    repository_id: UUID
    snapshot_id: UUID
    attention_profile_id: UUID | None
    pack_type: ContextPackType
    artifact_uri: str
    content_hash: str | None
    sources: list[ContextPackSourceResponse]
    created_at: datetime

    @classmethod
    def from_records(
        cls,
        context_pack: ContextPackRead,
        sources: list[ContextPackSourceRead],
    ) -> "ContextPackResponse":
        return cls(
            id=context_pack.id,
            repository_id=context_pack.repository_id,
            snapshot_id=context_pack.snapshot_id,
            attention_profile_id=context_pack.attention_profile_id,
            pack_type=context_pack.pack_type,
            artifact_uri=context_pack.artifact_uri,
            content_hash=context_pack.content_hash,
            sources=[ContextPackSourceResponse.from_record(source) for source in sources],
            created_at=context_pack.created_at,
        )


def infer_repository_name(source_uri: str) -> str:
    trimmed = source_uri.rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[:-4]
    name = PurePath(trimmed).name
    return name or "repository"
