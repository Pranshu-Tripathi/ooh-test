from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePath
from uuid import UUID

from ooh.db.models import (
    DriftEventRead,
    GeneratedTestRead,
    GuidanceSourceRead,
    JobRead,
    JobType,
    RepoSnapshotRead,
    RepositoryRead,
    RepositorySourceType,
)
from ooh.db.repos import (
    AttentionFocusAreaInput,
    AttentionProfileRepo,
    AttentionProfileWithFocusAreas,
    ContextPackRepo,
    ContextPackWithSources,
    DriftEventRepo,
    GeneratedTestRepo,
    GuidanceSourceRepo,
    JobRepo,
    RepoSnapshotRepo,
    RepositoryRepo,
)
from ooh.worker.context_pack_builder import ContextPackBuilder
from ooh.worker.drift_scorer import AttentionFocusWeight, AttentionProfileWeights, GitDriftScorer
from ooh.worker.repository_inspector import LocalRepositoryInspector
from ooh.worker.repository_source_resolver import RepositorySourceResolver
from ooh.services.exceptions import ConflictError, NotFoundError


@dataclass(frozen=True)
class RepositoryRegistration:
    repository: RepositoryRead
    ingest_job: JobRead


@dataclass(frozen=True)
class RepositoryIngestionResult:
    repository: RepositoryRead
    snapshot: RepoSnapshotRead
    commit_sha: str
    file_count: int
    guidance_sources: list[GuidanceSourceRead]
    drift_recorded: bool
    drift_score: str
    drift_severity: str


class RepositoryService:
    def __init__(
        self,
        *,
        repository_repo: RepositoryRepo,
        attention_profile_repo: AttentionProfileRepo,
        context_pack_repo: ContextPackRepo,
        repo_snapshot_repo: RepoSnapshotRepo,
        guidance_source_repo: GuidanceSourceRepo,
        job_repo: JobRepo,
        drift_event_repo: DriftEventRepo,
        generated_test_repo: GeneratedTestRepo,
        context_pack_builder: ContextPackBuilder,
        repository_source_resolver: RepositorySourceResolver | None = None,
        repository_inspector: LocalRepositoryInspector | None = None,
        drift_scorer: GitDriftScorer | None = None,
    ) -> None:
        self.repository_repo = repository_repo
        self.attention_profile_repo = attention_profile_repo
        self.context_pack_repo = context_pack_repo
        self.repo_snapshot_repo = repo_snapshot_repo
        self.guidance_source_repo = guidance_source_repo
        self.job_repo = job_repo
        self.drift_event_repo = drift_event_repo
        self.generated_test_repo = generated_test_repo
        self.context_pack_builder = context_pack_builder
        self.repository_source_resolver = repository_source_resolver
        self.repository_inspector = repository_inspector
        self.drift_scorer = drift_scorer

    def register_repository(
        self,
        *,
        name: str | None,
        source_type: RepositorySourceType,
        source_uri: str,
        default_branch: str | None = None,
        token_ref: str | None = None,
    ) -> RepositoryRegistration:
        repository, ingest_job = self.repository_repo.register_with_ingest_job(
            name=name or infer_repository_name(source_uri),
            source_type=source_type,
            source_uri=source_uri,
            default_branch=default_branch,
            token_ref=token_ref,
        )
        return RepositoryRegistration(repository=repository, ingest_job=ingest_job)

    def list_repositories(self) -> list[RepositoryRead]:
        return self.repository_repo.list_all()

    def get_repository(self, repository_id: UUID) -> RepositoryRead:
        return self._get_repository(repository_id)

    def enqueue_repository_ingest(self, repository_id: UUID) -> JobRead:
        repository = self._get_repository(repository_id)
        return self.job_repo.enqueue(
            repository_id=repository_id,
            job_type=JobType.INGEST_REPOSITORY,
            payload={
                "repository_id": str(repository.id),
                "source_type": repository.source_type.value,
                "source_uri": repository.source_uri,
            },
        )

    def mark_repository_failed(self, repository_id: UUID) -> RepositoryRead:
        return self.repository_repo.mark_failed(repository_id)

    def ingest_repository_job(self, job: JobRead) -> RepositoryIngestionResult:
        if job.repository_id is None:
            raise ValueError("ingest_repository job requires repository_id")
        if self.repository_source_resolver is None:
            raise RuntimeError("repository source resolver is not configured")
        if self.repository_inspector is None:
            raise RuntimeError("repository inspector is not configured")
        if self.drift_scorer is None:
            raise RuntimeError("drift scorer is not configured")

        self.repository_repo.mark_indexing(job.repository_id)
        repository = self._get_repository(job.repository_id)
        resolved_source = self.repository_source_resolver.resolve(repository)
        snapshot = self.repository_inspector.inspect_path(repository, resolved_source.path)
        snapshot_record = self.repo_snapshot_repo.create(
            repository_id=job.repository_id,
            commit_sha=snapshot.commit_sha,
            index_uri=snapshot.index_uri,
        )
        drift_summary = self.drift_scorer.score(
            resolved_source.path,
            from_commit_sha=repository.last_processed_commit_sha,
            to_commit_sha=snapshot.commit_sha,
            attention_profile=self.active_attention_profile_weights(job.repository_id),
        )
        if drift_summary.should_record:
            self.drift_event_repo.create(
                repository_id=job.repository_id,
                snapshot_id=snapshot_record.id,
                from_commit_sha=drift_summary.from_commit_sha,
                to_commit_sha=drift_summary.to_commit_sha,
                drift_score=drift_summary.drift_score,
                severity=drift_summary.severity,
                breakdown=drift_summary.breakdown,
            )
        guidance_sources = self.guidance_source_repo.replace_for_repository(
            job.repository_id,
            [
                (guidance.source_type, guidance.path, guidance.content_hash)
                for guidance in snapshot.guidance_sources
            ],
        )
        indexed_repository = self.repository_repo.mark_indexed_at_commit(
            job.repository_id,
            commit_sha=snapshot.commit_sha,
        )
        return RepositoryIngestionResult(
            repository=indexed_repository,
            snapshot=snapshot_record,
            commit_sha=snapshot.commit_sha,
            file_count=snapshot.file_count,
            guidance_sources=guidance_sources,
            drift_recorded=drift_summary.should_record,
            drift_score=str(drift_summary.drift_score),
            drift_severity=drift_summary.severity.value,
        )

    def create_attention_profile(
        self,
        *,
        repository_id: UUID,
        name: str,
        default_weight: Decimal,
        active: bool,
        focus_areas: list[AttentionFocusAreaInput],
    ) -> AttentionProfileWithFocusAreas:
        self._get_repository(repository_id)
        return self.attention_profile_repo.create(
            repository_id=repository_id,
            name=name,
            default_weight=default_weight,
            active=active,
            focus_areas=focus_areas,
        )

    def list_attention_profiles(self, repository_id: UUID) -> list[AttentionProfileWithFocusAreas]:
        self._get_repository(repository_id)
        return self.attention_profile_repo.list_for_repository(repository_id)

    def activate_attention_profile(
        self,
        *,
        repository_id: UUID,
        profile_id: UUID,
    ) -> AttentionProfileWithFocusAreas:
        self._get_repository(repository_id)
        try:
            return self.attention_profile_repo.activate(
                repository_id=repository_id,
                profile_id=profile_id,
            )
        except ValueError:
            raise NotFoundError("attention profile not found") from None

    def build_context_packs(self, repository_id: UUID) -> list[ContextPackWithSources]:
        repository = self._get_repository(repository_id)
        snapshot = self.repo_snapshot_repo.latest_for_repository(repository_id)
        if snapshot is None:
            raise ConflictError("repository has no snapshots")
        return self.build_context_packs_for_snapshot(repository=repository, snapshot=snapshot)

    def list_context_packs(self, repository_id: UUID) -> list[ContextPackWithSources]:
        self._get_repository(repository_id)
        return self.context_pack_repo.list_for_repository(repository_id)

    def list_generated_tests(self, repository_id: UUID) -> list[GeneratedTestRead]:
        self._get_repository(repository_id)
        return self.generated_test_repo.list_for_repository(repository_id)

    def list_drift_events(self, repository_id: UUID) -> list[DriftEventRead]:
        self._get_repository(repository_id)
        return self.drift_event_repo.list_for_repository(repository_id)

    def latest_snapshot(self, repository_id: UUID) -> RepoSnapshotRead:
        self._get_repository(repository_id)
        snapshot = self.repo_snapshot_repo.latest_for_repository(repository_id)
        if snapshot is None:
            raise ConflictError("repository has no snapshots")
        return snapshot

    def active_attention_profile_weights(
        self,
        repository_id: UUID,
    ) -> AttentionProfileWeights | None:
        active_profile = self.attention_profile_repo.get_active_for_repository(repository_id)
        if active_profile is None:
            return None
        return attention_profile_weights(active_profile)

    def build_context_packs_for_snapshot(
        self,
        *,
        repository: RepositoryRead,
        snapshot: RepoSnapshotRead,
        drift_event: DriftEventRead | None = None,
    ) -> list[ContextPackWithSources]:
        active_profile = self.attention_profile_repo.get_active_for_repository(repository.id)
        drift_event = drift_event or self.drift_event_repo.latest_for_repository(repository.id)
        guidance_sources = self.guidance_source_repo.list_enabled_for_repository(repository.id)
        built_packs = self.context_pack_builder.build(
            repository=repository,
            snapshot=snapshot,
            drift_event=drift_event,
            guidance_sources=guidance_sources,
            attention_profile=active_profile.profile if active_profile is not None else None,
            attention_focus_areas=active_profile.focus_areas if active_profile is not None else [],
        )
        return [
            self.context_pack_repo.create(
                repository_id=repository.id,
                snapshot_id=snapshot.id,
                attention_profile_id=active_profile.profile.id if active_profile is not None else None,
                pack_type=built_pack.pack_type,
                artifact_uri=built_pack.artifact_uri,
                content_hash=built_pack.content_hash,
                sources=built_pack.sources,
            )
            for built_pack in built_packs
        ]

    def _get_repository(self, repository_id: UUID) -> RepositoryRead:
        repository = self.repository_repo.get(repository_id)
        if repository is None:
            raise NotFoundError("repository not found")
        return repository


def attention_profile_weights(
    profile_with_focus_areas: AttentionProfileWithFocusAreas,
) -> AttentionProfileWeights:
    return AttentionProfileWeights(
        name=profile_with_focus_areas.profile.name,
        default_weight=profile_with_focus_areas.profile.default_weight,
        focus_areas=[
            AttentionFocusWeight(
                name=focus_area.name,
                weight=focus_area.weight,
                path_globs=focus_area.path_globs,
            )
            for focus_area in profile_with_focus_areas.focus_areas
        ],
    )


def infer_repository_name(source_uri: str) -> str:
    trimmed = source_uri.rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[:-4]
    name = PurePath(trimmed).name
    return name or "repository"
