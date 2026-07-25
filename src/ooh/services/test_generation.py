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
from ooh.services.exceptions import ConflictError, NotFoundError
from ooh.services.repositories import RepositoryService


@dataclass(frozen=True)
class TestGenerationJobResult:
    run_result: GeneratedTestRunResult
    selected_context_packs: list[ContextPackWithSources]


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
        generated_test_run_service: GeneratedTestRunService | None = None,
    ) -> None:
        self.repository_service = repository_service
        self.repository_repo = repository_repo
        self.repo_snapshot_repo = repo_snapshot_repo
        self.context_pack_repo = context_pack_repo
        self.drift_event_repo = drift_event_repo
        self.generated_test_repo = generated_test_repo
        self.job_repo = job_repo
        self.generated_test_run_service = generated_test_run_service

    def enqueue_generate_test_job(
        self,
        repository_id: UUID,
        *,
        pack_types: list[ContextPackType] | None,
    ) -> JobRead:
        self._get_repository(repository_id)
        snapshot = self.repo_snapshot_repo.latest_for_repository(repository_id)
        if snapshot is None:
            raise ConflictError("repository has no snapshots")

        payload = {
            "repository_id": str(repository_id),
            "snapshot_id": str(snapshot.id),
        }
        if pack_types is not None:
            payload["pack_types"] = [pack_type.value for pack_type in pack_types]

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
        requested_pack_types = requested_pack_types_from_job(job)
        selected_packs = [
            context_pack
            for context_pack in created_packs
            if requested_pack_types is None
            or context_pack.context_pack.pack_type in requested_pack_types
        ]
        if not selected_packs:
            raise ValueError("generate_test job did not select any context packs")

        run_result = self.generated_test_run_service.generate_for_context_packs(
            selected_packs,
            job_id=job.id,
        )
        return TestGenerationJobResult(
            run_result=run_result,
            selected_context_packs=selected_packs,
        )

    def _get_repository(self, repository_id: UUID) -> RepositoryRead:
        repository = self.repository_repo.get(repository_id)
        if repository is None:
            raise NotFoundError("repository not found")
        return repository


def requested_pack_types_from_job(job: JobRead) -> set[ContextPackType] | None:
    raw_pack_types = job.payload.get("pack_types")
    if raw_pack_types is None:
        return None
    if not isinstance(raw_pack_types, list):
        raise ValueError("generate_test payload pack_types must be a list")

    pack_types: set[ContextPackType] = set()
    for raw_pack_type in raw_pack_types:
        if not isinstance(raw_pack_type, str):
            raise ValueError("generate_test payload pack_types must contain strings")
        pack_types.add(ContextPackType(raw_pack_type))
    return pack_types or None


def payload_optional_uuid(job: JobRead, key: str) -> UUID | None:
    raw_value = job.payload.get(key)
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise ValueError(f"{job.job_type.value} payload {key} must be a string")
    return UUID(raw_value)
