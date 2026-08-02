from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ooh.agent.test_generation_runner import GeneratedTestRunResult, GeneratedTestRunService
from ooh.db.models import ContextPackType, GeneratedTestRead, JobRead, JobType, RepositoryRead
from ooh.db.repos import (
    ContextPackRepo,
    ContextPackWithSources,
    DriftEventRepo,
    GeneratedTestRepo,
    JobRepo,
    RepoSnapshotRepo,
    RepositoryRepo,
)
from ooh.generation_config import (
    DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY,
    MAX_GENERATION_QUESTIONS_PER_CATEGORY,
    MAX_GENERATION_QUESTIONS_PER_JOB,
    validate_default_generation_questions_per_category,
)
from ooh.services.exceptions import ConflictError, NotFoundError
from ooh.services.repositories import RepositoryService


@dataclass(frozen=True)
class TestGenerationJobResult:
    run_result: GeneratedTestRunResult
    selected_context_packs: list[ContextPackWithSources]
    question_counts: dict[ContextPackType, int]


class TestGenerationService:
    def __init__(
        self,
        *,
        repository_service: RepositoryService,
        repository_repo: RepositoryRepo,
        repo_snapshot_repo: RepoSnapshotRepo,
        context_pack_repo: ContextPackRepo,
        drift_event_repo: DriftEventRepo,
        generated_test_repo: GeneratedTestRepo,
        job_repo: JobRepo,
        questions_per_category_default: int = DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY,
        generated_test_run_service: GeneratedTestRunService | None = None,
    ) -> None:
        self.repository_service = repository_service
        self.repository_repo = repository_repo
        self.repo_snapshot_repo = repo_snapshot_repo
        self.context_pack_repo = context_pack_repo
        self.drift_event_repo = drift_event_repo
        self.generated_test_repo = generated_test_repo
        self.job_repo = job_repo
        self.questions_per_category_default = (
            validate_default_generation_questions_per_category(
                questions_per_category_default
            )
        )
        self.generated_test_run_service = generated_test_run_service

    def enqueue_generate_test_job(
        self,
        repository_id: UUID,
        *,
        pack_types: list[ContextPackType] | None,
        question_counts: dict[ContextPackType, int] | None = None,
    ) -> JobRead:
        self._get_repository(repository_id)
        snapshot = self.repo_snapshot_repo.latest_for_repository(repository_id)
        if snapshot is None:
            raise ConflictError("repository has no snapshots")

        if question_counts is not None and pack_types is not None:
            raise ValueError("provide question_counts or pack_types, not both")
        if question_counts is None:
            selected_pack_types = pack_types or list(ContextPackType)
            question_counts = {
                pack_type: self.questions_per_category_default
                for pack_type in selected_pack_types
            }
        question_counts = validate_question_counts(question_counts)

        payload = {
            "repository_id": str(repository_id),
            "snapshot_id": str(snapshot.id),
            "generation_plan": [
                {
                    "category": pack_type.value,
                    "question_count": question_count,
                }
                for pack_type, question_count in question_counts.items()
            ],
        }

        return self.job_repo.enqueue(
            repository_id=repository_id,
            job_type=JobType.GENERATE_TEST,
            payload=payload,
        )

    def list_generated_tests(self, repository_id: UUID) -> list[GeneratedTestRead]:
        self._get_repository(repository_id)
        return self.generated_test_repo.list_for_repository(repository_id)

    def run_generation_job(self, job: JobRead) -> TestGenerationJobResult:
        if job.repository_id is None:
            raise ValueError("generate_test job requires repository_id")
        if self.generated_test_run_service is None:
            raise RuntimeError("generated test runner is not configured")

        repository = self._get_repository(job.repository_id)
        snapshot_id = payload_optional_uuid(job, "snapshot_id")
        snapshot = (
            self.repo_snapshot_repo.get(snapshot_id)
            if snapshot_id is not None
            else self.repo_snapshot_repo.latest_for_repository(job.repository_id)
        )
        if snapshot is None or snapshot.repository_id != job.repository_id:
            raise ValueError(f"repository snapshot is unavailable: {snapshot_id}")

        drift_event_id = payload_optional_uuid(job, "drift_event_id")
        drift_event = (
            self.drift_event_repo.get(drift_event_id)
            if drift_event_id is not None
            else None
        )
        if drift_event is not None and (
            drift_event.repository_id != job.repository_id
            or drift_event.snapshot_id != snapshot.id
        ):
            raise ValueError("drift event does not belong to the requested snapshot")
        if drift_event_id is not None and drift_event is None:
            raise ValueError(f"drift event is unavailable: {drift_event_id}")

        created_packs = self.repository_service.build_context_packs_for_snapshot(
            repository=repository,
            snapshot=snapshot,
            drift_event=drift_event,
        )
        question_counts = requested_question_counts_from_job(
            job,
            default_questions_per_category=self.questions_per_category_default,
        )
        if question_counts is None:
            question_counts = {
                context_pack.context_pack.pack_type: self.questions_per_category_default
                for context_pack in created_packs
            }
        requested_pack_types = set(question_counts)
        selected_packs = [
            context_pack
            for context_pack in created_packs
            if context_pack.context_pack.pack_type in requested_pack_types
        ]
        if not selected_packs:
            raise ValueError("generate_test job did not select any context packs")
        selected_pack_types = {
            context_pack.context_pack.pack_type for context_pack in selected_packs
        }
        missing_pack_types = requested_pack_types - selected_pack_types
        if missing_pack_types:
            missing_values = ", ".join(sorted(pack_type.value for pack_type in missing_pack_types))
            raise ValueError(f"generate_test job could not build requested context packs: {missing_values}")

        run_result = self.generated_test_run_service.generate_for_context_packs(
            selected_packs,
            job_id=job.id,
            question_counts=question_counts,
        )
        return TestGenerationJobResult(
            run_result=run_result,
            selected_context_packs=selected_packs,
            question_counts=question_counts,
        )

    def _get_repository(self, repository_id: UUID) -> RepositoryRead:
        repository = self.repository_repo.get(repository_id)
        if repository is None:
            raise NotFoundError("repository not found")
        return repository


def requested_pack_types_from_job(job: JobRead) -> set[ContextPackType] | None:
    question_counts = requested_question_counts_from_job(job)
    return set(question_counts) if question_counts is not None else None


def requested_question_counts_from_job(
    job: JobRead,
    *,
    default_questions_per_category: int = DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY,
) -> dict[ContextPackType, int] | None:
    raw_plan = job.payload.get("generation_plan")
    if raw_plan is not None:
        if not isinstance(raw_plan, list):
            raise ValueError("generate_test payload generation_plan must be a list")
        question_counts: dict[ContextPackType, int] = {}
        for raw_item in raw_plan:
            if not isinstance(raw_item, dict):
                raise ValueError("generate_test payload generation_plan must contain objects")
            raw_category = raw_item.get("category")
            raw_question_count = raw_item.get("question_count")
            if not isinstance(raw_category, str):
                raise ValueError("generate_test payload generation_plan categories must be strings")
            if isinstance(raw_question_count, bool) or not isinstance(raw_question_count, int):
                raise ValueError(
                    "generate_test payload generation_plan question_count must be an integer"
                )
            pack_type = ContextPackType(raw_category)
            if pack_type in question_counts:
                raise ValueError(
                    "generate_test payload generation_plan categories must not contain duplicates"
                )
            question_counts[pack_type] = raw_question_count
        if not question_counts:
            raise ValueError("generate_test payload generation_plan must not be empty")
        return validate_question_counts(question_counts)

    raw_pack_types = job.payload.get("pack_types")
    if raw_pack_types is None:
        return None
    if not isinstance(raw_pack_types, list):
        raise ValueError("generate_test payload pack_types must be a list")

    question_counts: dict[ContextPackType, int] = {}
    for raw_pack_type in raw_pack_types:
        if not isinstance(raw_pack_type, str):
            raise ValueError("generate_test payload pack_types must contain strings")
        question_counts[ContextPackType(raw_pack_type)] = default_questions_per_category
    return question_counts or None


def validate_question_counts(
    question_counts: dict[ContextPackType, int],
) -> dict[ContextPackType, int]:
    if not question_counts:
        raise ValueError("at least one generation category is required")
    for pack_type, question_count in question_counts.items():
        if not isinstance(pack_type, ContextPackType):
            raise ValueError("generation categories must be context pack types")
        if isinstance(question_count, bool) or not isinstance(question_count, int):
            raise ValueError("question counts must be integers")
        if question_count < 1 or question_count > MAX_GENERATION_QUESTIONS_PER_CATEGORY:
            raise ValueError(
                "question count per category must be between "
                f"1 and {MAX_GENERATION_QUESTIONS_PER_CATEGORY}"
            )
    total_question_count = sum(question_counts.values())
    if total_question_count > MAX_GENERATION_QUESTIONS_PER_JOB:
        raise ValueError(
            "generation job cannot request more than "
            f"{MAX_GENERATION_QUESTIONS_PER_JOB} questions"
        )
    return dict(question_counts)


def payload_optional_uuid(job: JobRead, key: str) -> UUID | None:
    raw_value = job.payload.get(key)
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise ValueError(f"{job.job_type.value} payload {key} must be a string")
    return UUID(raw_value)
