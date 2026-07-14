import logging
from uuid import UUID

from ooh.agent.answer_judging_runner import AnswerJudgingRunService
from ooh.agent.artifacts import AgentArtifactStore
from ooh.agent.providers import build_model_provider
from ooh.agent.test_generation_runner import GeneratedTestRunService
from ooh.config import get_settings
from ooh.db import Database
from ooh.db.models import ContextPackType, JobRead, JobStatus, JobType
from ooh.db.repos import (
    AgentTraceRepo,
    AttentionProfileRepo,
    AttentionProfileWithFocusAreas,
    ContextPackRepo,
    DriftEventRepo,
    GeneratedTestRepo,
    GuidanceSourceRepo,
    JobRepo,
    RepoSnapshotRepo,
    RepositoryRepo,
    SavedLearningRepo,
    TestAnswerRepo,
    TestResultRepo,
)
from ooh.worker.context_pack_builder import ContextPackBuilder
from ooh.worker.drift_scorer import AttentionFocusWeight, AttentionProfileWeights, GitDriftScorer
from ooh.worker.repository_inspector import LocalRepositoryInspector
from ooh.worker.repository_source_resolver import RepositorySourceResolver

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, db: Database, *, worker_id: str) -> None:
        settings = get_settings()
        self.worker_id = worker_id
        self.job_repo = JobRepo(db)
        self.repository_repo = RepositoryRepo(db)
        self.attention_profile_repo = AttentionProfileRepo(db)
        self.repo_snapshot_repo = RepoSnapshotRepo(db)
        self.drift_event_repo = DriftEventRepo(db)
        self.guidance_source_repo = GuidanceSourceRepo(db)
        self.context_pack_repo = ContextPackRepo(db)
        self.agent_trace_repo = AgentTraceRepo(db)
        self.generated_test_repo = GeneratedTestRepo(db)
        self.test_answer_repo = TestAnswerRepo(db)
        self.test_result_repo = TestResultRepo(db)
        self.saved_learning_repo = SavedLearningRepo(db)
        self.repository_source_resolver = RepositorySourceResolver(cache_root=settings.cache_root)
        self.repository_inspector = LocalRepositoryInspector(cache_root=settings.cache_root)
        self.context_pack_builder = ContextPackBuilder(cache_root=settings.cache_root)
        self.drift_scorer = GitDriftScorer()
        model_provider = build_model_provider(settings)
        artifact_store = AgentArtifactStore(cache_root=settings.cache_root)
        self.generated_test_run_service = GeneratedTestRunService(
            provider=model_provider,
            model=settings.test_generator_model,
            artifact_store=artifact_store,
            agent_trace_repo=self.agent_trace_repo,
            generated_test_repo=self.generated_test_repo,
        )
        self.answer_judging_run_service = AnswerJudgingRunService(
            provider=model_provider,
            model=settings.answer_judge_model,
            artifact_store=artifact_store,
            agent_trace_repo=self.agent_trace_repo,
            test_result_repo=self.test_result_repo,
            saved_learning_repo=self.saved_learning_repo,
        )

    def process_once(self) -> bool:
        job = self.job_repo.claim_next(worker_id=self.worker_id)
        if job is None:
            return False

        logger.info(
            "claimed job",
            extra={"job_id": str(job.id), "job_type": job.job_type.value, "worker_id": self.worker_id},
        )

        try:
            self.dispatch(job)
        except Exception as exc:
            logger.exception("job failed", extra={"job_id": str(job.id), "job_type": job.job_type.value})
            failed_job = self.job_repo.mark_failed(job.id, error_summary=str(exc))
            if (
                failed_job.status == JobStatus.FAILED
                and failed_job.job_type == JobType.INGEST_REPOSITORY
                and failed_job.repository_id is not None
            ):
                self.repository_repo.mark_failed(failed_job.repository_id)
            return True

        self.job_repo.mark_succeeded(job.id)
        logger.info("job succeeded", extra={"job_id": str(job.id), "job_type": job.job_type.value})
        return True

    def dispatch(self, job: JobRead) -> None:
        if job.job_type == JobType.INGEST_REPOSITORY:
            self.ingest_repository(job)
            return
        if job.job_type == JobType.GENERATE_TEST:
            self.generate_tests(job)
            return
        if job.job_type == JobType.JUDGE_ANSWER:
            self.judge_answer(job)
            return

        raise ValueError(f"unsupported job type: {job.job_type.value}")

    def ingest_repository(self, job: JobRead) -> None:
        if job.repository_id is None:
            raise ValueError("ingest_repository job requires repository_id")

        self.repository_repo.mark_indexing(job.repository_id)
        repository = self.repository_repo.get(job.repository_id)
        if repository is None:
            raise ValueError(f"repository not found: {job.repository_id}")

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
            attention_profile=self._active_attention_profile_weights(job.repository_id),
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
        self.guidance_source_repo.replace_for_repository(
            job.repository_id,
            [
                (guidance.source_type, guidance.path, guidance.content_hash)
                for guidance in snapshot.guidance_sources
            ],
        )
        self.repository_repo.mark_indexed_at_commit(job.repository_id, commit_sha=snapshot.commit_sha)
        logger.info(
            "repository indexed",
            extra={
                "repository_id": str(job.repository_id),
                "commit_sha": snapshot.commit_sha,
                "file_count": snapshot.file_count,
                "guidance_source_count": len(snapshot.guidance_sources),
                "drift_score": str(drift_summary.drift_score),
                "drift_severity": drift_summary.severity.value,
                "drift_recorded": drift_summary.should_record,
            },
        )

    def generate_tests(self, job: JobRead) -> None:
        if job.repository_id is None:
            raise ValueError("generate_test job requires repository_id")

        repository = self.repository_repo.get(job.repository_id)
        if repository is None:
            raise ValueError(f"repository not found: {job.repository_id}")

        snapshot = self.repo_snapshot_repo.latest_for_repository(job.repository_id)
        if snapshot is None:
            raise ValueError(f"repository has no snapshots: {job.repository_id}")

        active_profile = self.attention_profile_repo.get_active_for_repository(job.repository_id)
        drift_event = self.drift_event_repo.latest_for_repository(job.repository_id)
        guidance_sources = self.guidance_source_repo.list_enabled_for_repository(job.repository_id)
        requested_pack_types = self._requested_pack_types(job)
        built_packs = self.context_pack_builder.build(
            repository=repository,
            snapshot=snapshot,
            drift_event=drift_event,
            guidance_sources=guidance_sources,
            attention_profile=active_profile.profile if active_profile is not None else None,
            attention_focus_areas=active_profile.focus_areas if active_profile is not None else [],
        )
        created_packs = [
            self.context_pack_repo.create(
                repository_id=job.repository_id,
                snapshot_id=snapshot.id,
                attention_profile_id=active_profile.profile.id if active_profile is not None else None,
                pack_type=built_pack.pack_type,
                artifact_uri=built_pack.artifact_uri,
                content_hash=built_pack.content_hash,
                sources=built_pack.sources,
            )
            for built_pack in built_packs
        ]
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
        logger.info(
            "generated tests",
            extra={
                "repository_id": str(job.repository_id),
                "job_id": str(job.id),
                "agent_run_id": str(run_result.agent_run.id),
                "generated_test_count": len(run_result.generated_tests),
                "context_pack_ids": [
                    str(context_pack.context_pack.id) for context_pack in selected_packs
                ],
            },
        )

    def judge_answer(self, job: JobRead) -> None:
        generated_test_id = self._payload_uuid(job, "generated_test_id")
        test_answer_id = self._payload_uuid(job, "test_answer_id")

        generated_test = self.generated_test_repo.get(generated_test_id)
        if generated_test is None:
            raise ValueError(f"generated test not found: {generated_test_id}")

        test_answer = self.test_answer_repo.get(test_answer_id)
        if test_answer is None:
            raise ValueError(f"test answer not found: {test_answer_id}")
        if test_answer.generated_test_id != generated_test.id:
            raise ValueError("test answer does not belong to generated test")

        run_result = self.answer_judging_run_service.judge_answer(
            generated_test=generated_test,
            test_answer=test_answer,
            job_id=job.id,
        )
        logger.info(
            "judged answer",
            extra={
                "repository_id": str(generated_test.repository_id),
                "job_id": str(job.id),
                "agent_run_id": str(run_result.agent_run.id),
                "generated_test_id": str(generated_test.id),
                "test_answer_id": str(test_answer.id),
                "test_result_id": str(run_result.test_result.id),
                "score": str(run_result.test_result.score),
                "status": run_result.test_result.status.value,
            },
        )

    @staticmethod
    def _requested_pack_types(job: JobRead) -> set[ContextPackType] | None:
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

    @staticmethod
    def _payload_uuid(job: JobRead, key: str) -> UUID:
        raw_value = job.payload.get(key)
        if not isinstance(raw_value, str):
            raise ValueError(f"{job.job_type.value} payload requires string {key}")
        return UUID(raw_value)

    def _active_attention_profile_weights(self, repository_id: UUID) -> AttentionProfileWeights | None:
        active_profile = self.attention_profile_repo.get_active_for_repository(repository_id)
        if active_profile is None:
            return None
        return self._attention_profile_weights(active_profile)

    @staticmethod
    def _attention_profile_weights(
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
