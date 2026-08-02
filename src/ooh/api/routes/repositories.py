from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from ooh.api.schemas.repositories import (
    AttentionProfileCreateRequest,
    AttentionProfileResponse,
    ContextPackResponse,
    DriftEventResponse,
    GeneratedTestResponse,
    GenerateTestJobRequest,
    JobResponse,
    RepositoryCreateRequest,
    RepositoryRegistrationResponse,
    RepositoryResponse,
    infer_repository_name,
)
from ooh.config import get_settings
from ooh.db import get_database
from ooh.db.models import JobType
from ooh.db.repos import (
    AttentionFocusAreaInput,
    AttentionProfileRepo,
    ContextPackRepo,
    DriftEventRepo,
    GeneratedTestRepo,
    GuidanceSourceRepo,
    JobRepo,
    RepoSnapshotRepo,
    RepositoryRepo,
)
from ooh.worker.context_pack_builder import ContextPackBuilder

router = APIRouter(prefix="/repositories", tags=["repositories"])
repository_repo = RepositoryRepo(get_database())
attention_profile_repo = AttentionProfileRepo(get_database())
context_pack_repo = ContextPackRepo(get_database())
repo_snapshot_repo = RepoSnapshotRepo(get_database())
guidance_source_repo = GuidanceSourceRepo(get_database())
job_repo = JobRepo(get_database())
drift_event_repo = DriftEventRepo(get_database())
generated_test_repo = GeneratedTestRepo(get_database())
context_pack_builder = ContextPackBuilder(cache_root=get_settings().cache_root)


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


@router.post("/{repository_id}/ingest-jobs", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def enqueue_repository_ingest(repository_id: UUID) -> JobResponse:
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")

    ingest_job = job_repo.enqueue(
        repository_id=repository_id,
        job_type=JobType.INGEST_REPOSITORY,
        payload={
            "repository_id": str(repository.id),
            "source_type": repository.source_type.value,
            "source_uri": repository.source_uri,
        },
    )
    return JobResponse.from_record(ingest_job)


@router.post(
    "/{repository_id}/generate-test-jobs",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_generate_test_job(
    repository_id: UUID,
    request: GenerateTestJobRequest | None = None,
) -> JobResponse:
    request = request or GenerateTestJobRequest()
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")

    snapshot = repo_snapshot_repo.latest_for_repository(repository_id)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="repository has no snapshots")

    payload = {
        "repository_id": str(repository_id),
        "snapshot_id": str(snapshot.id),
    }
    if request.pack_types is not None:
        payload["pack_types"] = [pack_type.value for pack_type in request.pack_types]

    generate_job = job_repo.enqueue(
        repository_id=repository_id,
        job_type=JobType.GENERATE_TEST,
        payload=payload,
    )
    return JobResponse.from_record(generate_job)


@router.post(
    "/{repository_id}/attention-profiles",
    response_model=AttentionProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_attention_profile(
    repository_id: UUID,
    request: AttentionProfileCreateRequest,
) -> AttentionProfileResponse:
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")

    profile = attention_profile_repo.create(
        repository_id=repository_id,
        name=request.name,
        default_weight=request.default_weight,
        active=request.active,
        focus_areas=[
            AttentionFocusAreaInput(
                name=focus_area.name,
                description=focus_area.description,
                weight=focus_area.weight,
                path_globs=focus_area.path_globs,
            )
            for focus_area in request.focus_areas
        ],
    )
    return AttentionProfileResponse.from_records(profile.profile, profile.focus_areas)


@router.get("/{repository_id}/attention-profiles", response_model=list[AttentionProfileResponse])
def list_attention_profiles(repository_id: UUID) -> list[AttentionProfileResponse]:
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")

    profiles = attention_profile_repo.list_for_repository(repository_id)
    return [AttentionProfileResponse.from_records(profile.profile, profile.focus_areas) for profile in profiles]


@router.post(
    "/{repository_id}/attention-profiles/{profile_id}/activate",
    response_model=AttentionProfileResponse,
)
def activate_attention_profile(repository_id: UUID, profile_id: UUID) -> AttentionProfileResponse:
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")

    try:
        profile = attention_profile_repo.activate(repository_id=repository_id, profile_id=profile_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="attention profile not found")
    return AttentionProfileResponse.from_records(profile.profile, profile.focus_areas)


@router.post("/{repository_id}/context-packs", response_model=list[ContextPackResponse])
def build_context_packs(repository_id: UUID) -> list[ContextPackResponse]:
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")

    snapshot = repo_snapshot_repo.latest_for_repository(repository_id)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="repository has no snapshots")

    active_profile = attention_profile_repo.get_active_for_repository(repository_id)
    drift_event = drift_event_repo.latest_for_repository(repository_id)
    guidance_sources = guidance_source_repo.list_enabled_for_repository(repository_id)
    built_packs = context_pack_builder.build(
        repository=repository,
        snapshot=snapshot,
        drift_event=drift_event,
        guidance_sources=guidance_sources,
        attention_profile=active_profile.profile if active_profile is not None else None,
        attention_focus_areas=active_profile.focus_areas if active_profile is not None else [],
    )

    created_packs = [
        context_pack_repo.create(
            repository_id=repository_id,
            snapshot_id=snapshot.id,
            attention_profile_id=active_profile.profile.id if active_profile is not None else None,
            pack_type=built_pack.pack_type,
            artifact_uri=built_pack.artifact_uri,
            content_hash=built_pack.content_hash,
            sources=built_pack.sources,
        )
        for built_pack in built_packs
    ]
    return [ContextPackResponse.from_records(pack.context_pack, pack.sources) for pack in created_packs]


@router.get("/{repository_id}/context-packs", response_model=list[ContextPackResponse])
def list_context_packs(repository_id: UUID) -> list[ContextPackResponse]:
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")

    context_packs = context_pack_repo.list_for_repository(repository_id)
    return [ContextPackResponse.from_records(pack.context_pack, pack.sources) for pack in context_packs]


@router.get("/{repository_id}/generated-tests", response_model=list[GeneratedTestResponse])
def list_generated_tests(repository_id: UUID) -> list[GeneratedTestResponse]:
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")

    generated_tests = generated_test_repo.list_for_repository(repository_id)
    return [GeneratedTestResponse.from_record(generated_test) for generated_test in generated_tests]


@router.get("/{repository_id}/drift-events", response_model=list[DriftEventResponse])
def list_repository_drift_events(repository_id: UUID) -> list[DriftEventResponse]:
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")

    drift_events = drift_event_repo.list_for_repository(repository_id)
    return [DriftEventResponse.from_record(drift_event) for drift_event in drift_events]
