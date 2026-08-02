from uuid import UUID

from fastapi import APIRouter, status

from ooh.api.errors import raise_http_for_service_error
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
    RepositoryScheduleResponse,
    RepositoryScheduleUpdateRequest,
)
from ooh.db.repos import AttentionFocusAreaInput
from ooh.services import (
    ServiceError,
    build_repository_service,
    build_scheduler_service,
    build_test_generation_service,
)

router = APIRouter(prefix="/repositories", tags=["repositories"])
repository_service = build_repository_service()
test_generation_service = build_test_generation_service(
    repository_service=repository_service,
)
scheduler_service = build_scheduler_service()


@router.post("", response_model=RepositoryRegistrationResponse, status_code=status.HTTP_201_CREATED)
def register_repository(request: RepositoryCreateRequest) -> RepositoryRegistrationResponse:
    registration = repository_service.register_repository(
        name=request.name,
        source_type=request.source_type,
        source_uri=request.source_uri,
        default_branch=request.default_branch,
        token_ref=request.token_ref,
    )

    return RepositoryRegistrationResponse(
        repository=RepositoryResponse.from_record(registration.repository),
        ingest_job=JobResponse.from_record(registration.ingest_job),
    )


@router.get("", response_model=list[RepositoryResponse])
def list_repositories() -> list[RepositoryResponse]:
    repositories = repository_service.list_repositories()
    return [RepositoryResponse.from_record(repository) for repository in repositories]


@router.get("/{repository_id}", response_model=RepositoryResponse)
def get_repository(repository_id: UUID) -> RepositoryResponse:
    try:
        repository = repository_service.get_repository(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return RepositoryResponse.from_record(repository)


@router.post(
    "/{repository_id}/ingest-jobs", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED
)
def enqueue_repository_ingest(repository_id: UUID) -> JobResponse:
    try:
        ingest_job = repository_service.enqueue_repository_ingest(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
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
    try:
        generate_job = test_generation_service.enqueue_generate_test_job(
            repository_id,
            pack_types=request.pack_types,
            question_counts=(
                {item.category: item.question_count for item in request.generation_plan}
                if request.generation_plan is not None
                else None
            ),
        )
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return JobResponse.from_record(generate_job)


@router.get(
    "/{repository_id}/schedule",
    response_model=RepositoryScheduleResponse | None,
)
def get_repository_schedule(repository_id: UUID) -> RepositoryScheduleResponse | None:
    try:
        schedule = scheduler_service.get_repository_schedule(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return RepositoryScheduleResponse.from_record(schedule) if schedule is not None else None


@router.put(
    "/{repository_id}/schedule",
    response_model=RepositoryScheduleResponse,
)
def update_repository_schedule(
    repository_id: UUID,
    request: RepositoryScheduleUpdateRequest,
) -> RepositoryScheduleResponse:
    try:
        schedule = scheduler_service.update_repository_schedule(
            repository_id=repository_id,
            enabled=request.enabled,
            drift_min_score=request.drift_min_score,
            drift_max_score=request.drift_max_score,
            pack_types=request.pack_types,
            question_counts=(
                {item.category: item.question_count for item in request.generation_plan}
                if request.generation_plan is not None
                else None
            ),
            max_questions_per_trigger=request.max_questions_per_trigger,
        )
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return RepositoryScheduleResponse.from_record(schedule)


@router.post(
    "/{repository_id}/attention-profiles",
    response_model=AttentionProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_attention_profile(
    repository_id: UUID,
    request: AttentionProfileCreateRequest,
) -> AttentionProfileResponse:
    focus_areas = [
        AttentionFocusAreaInput(
            name=focus_area.name,
            description=focus_area.description,
            weight=focus_area.weight,
            path_globs=focus_area.path_globs,
        )
        for focus_area in request.focus_areas
    ]
    try:
        profile = repository_service.create_attention_profile(
            repository_id=repository_id,
            name=request.name,
            default_weight=request.default_weight,
            active=request.active,
            focus_areas=focus_areas,
        )
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return AttentionProfileResponse.from_records(profile.profile, profile.focus_areas)


@router.get("/{repository_id}/attention-profiles", response_model=list[AttentionProfileResponse])
def list_attention_profiles(repository_id: UUID) -> list[AttentionProfileResponse]:
    try:
        profiles = repository_service.list_attention_profiles(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [
        AttentionProfileResponse.from_records(profile.profile, profile.focus_areas)
        for profile in profiles
    ]


@router.post(
    "/{repository_id}/attention-profiles/{profile_id}/activate",
    response_model=AttentionProfileResponse,
)
def activate_attention_profile(repository_id: UUID, profile_id: UUID) -> AttentionProfileResponse:
    try:
        profile = repository_service.activate_attention_profile(
            repository_id=repository_id,
            profile_id=profile_id,
        )
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return AttentionProfileResponse.from_records(profile.profile, profile.focus_areas)


@router.post("/{repository_id}/context-packs", response_model=list[ContextPackResponse])
def build_context_packs(repository_id: UUID) -> list[ContextPackResponse]:
    try:
        created_packs = repository_service.build_context_packs(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [
        ContextPackResponse.from_records(pack.context_pack, pack.sources) for pack in created_packs
    ]


@router.get("/{repository_id}/context-packs", response_model=list[ContextPackResponse])
def list_context_packs(repository_id: UUID) -> list[ContextPackResponse]:
    try:
        context_packs = repository_service.list_context_packs(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [
        ContextPackResponse.from_records(pack.context_pack, pack.sources) for pack in context_packs
    ]


@router.get("/{repository_id}/generated-tests", response_model=list[GeneratedTestResponse])
def list_generated_tests(repository_id: UUID) -> list[GeneratedTestResponse]:
    try:
        generated_tests = test_generation_service.list_generated_tests(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [GeneratedTestResponse.from_record(generated_test) for generated_test in generated_tests]


@router.get("/{repository_id}/drift-events", response_model=list[DriftEventResponse])
def list_repository_drift_events(repository_id: UUID) -> list[DriftEventResponse]:
    try:
        drift_events = repository_service.list_drift_events(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [DriftEventResponse.from_record(drift_event) for drift_event in drift_events]
