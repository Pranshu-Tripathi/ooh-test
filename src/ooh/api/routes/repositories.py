from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from ooh.api.schemas.repositories import (
    JobResponse,
    RepositoryCreateRequest,
    RepositoryRegistrationResponse,
    RepositoryResponse,
    infer_repository_name,
)
from ooh.db import get_database
from ooh.db.repos import RepositoryRepo

router = APIRouter(prefix="/repositories", tags=["repositories"])
repository_repo = RepositoryRepo(get_database())


@router.post("", response_model=RepositoryRegistrationResponse, status_code=status.HTTP_201_CREATED)
def register_repository(request: RepositoryCreateRequest) -> RepositoryRegistrationResponse:
    repository, ingest_job = repository_repo.register_with_ingest_job(
        name=request.name or infer_repository_name(request.source_uri),
        source_type=request.source_type,
        source_uri=request.source_uri,
        default_branch=request.default_branch,
        token_ref=request.token_ref,
    )

    return RepositoryRegistrationResponse(
        repository=RepositoryResponse.from_record(repository),
        ingest_job=JobResponse.from_record(ingest_job),
    )


@router.get("", response_model=list[RepositoryResponse])
def list_repositories() -> list[RepositoryResponse]:
    repositories = repository_repo.list_all()
    return [RepositoryResponse.from_record(repository) for repository in repositories]


@router.get("/{repository_id}", response_model=RepositoryResponse)
def get_repository(repository_id: UUID) -> RepositoryResponse:
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")
    return RepositoryResponse.from_record(repository)
