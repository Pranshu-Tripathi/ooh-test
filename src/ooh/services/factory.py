from __future__ import annotations

from dataclasses import dataclass

from ooh.agent.answer_judging_runner import AnswerJudgingRunService
from ooh.agent.artifacts import AgentArtifactStore
from ooh.agent.providers import build_model_provider
from ooh.agent.test_generation_runner import GeneratedTestRunService
from ooh.config import Settings, get_settings
from ooh.db import Database, get_database
from ooh.db.repos import (
    AgentTraceRepo,
    AttentionProfileRepo,
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
from ooh.services.answer_judging import AnswerJudgingService
from ooh.services.jobs import JobService
from ooh.services.learnings import LearningService
from ooh.services.repositories import RepositoryService
from ooh.services.test_generation import TestGenerationService
from ooh.worker.context_pack_builder import ContextPackBuilder
from ooh.worker.drift_scorer import GitDriftScorer
from ooh.worker.repository_inspector import LocalRepositoryInspector
from ooh.worker.repository_source_resolver import RepositorySourceResolver


@dataclass(frozen=True)
class WorkerServices:
    jobs: JobService
    repositories: RepositoryService
    test_generation: TestGenerationService
    answer_judging: AnswerJudgingService


def build_job_service(db: Database | None = None) -> JobService:
    db = db or get_database()
    return JobService(job_repo=JobRepo(db))


def build_repository_service(
    db: Database | None = None,
    *,
    settings: Settings | None = None,
    include_worker_adapters: bool = False,
) -> RepositoryService:
    db = db or get_database()
    settings = settings or get_settings()
    repository_source_resolver = None
    repository_inspector = None
    drift_scorer = None
    if include_worker_adapters:
        repository_source_resolver = RepositorySourceResolver(cache_root=settings.cache_root)
        repository_inspector = LocalRepositoryInspector(cache_root=settings.cache_root)
        drift_scorer = GitDriftScorer()

    return RepositoryService(
        repository_repo=RepositoryRepo(db),
        attention_profile_repo=AttentionProfileRepo(db),
        context_pack_repo=ContextPackRepo(db),
        repo_snapshot_repo=RepoSnapshotRepo(db),
        guidance_source_repo=GuidanceSourceRepo(db),
        job_repo=JobRepo(db),
        drift_event_repo=DriftEventRepo(db),
        generated_test_repo=GeneratedTestRepo(db),
        context_pack_builder=ContextPackBuilder(cache_root=settings.cache_root),
        repository_source_resolver=repository_source_resolver,
        repository_inspector=repository_inspector,
        drift_scorer=drift_scorer,
    )


def build_test_generation_service(
    db: Database | None = None,
    *,
    settings: Settings | None = None,
    repository_service: RepositoryService | None = None,
    include_runner: bool = False,
) -> TestGenerationService:
    db = db or get_database()
    settings = settings or get_settings()
    repository_service = repository_service or build_repository_service(db, settings=settings)
    generated_test_run_service = None
    if include_runner:
        agent_trace_repo = AgentTraceRepo(db)
        generated_test_repo = GeneratedTestRepo(db)
        model_provider = build_model_provider(settings)
        artifact_store = AgentArtifactStore(cache_root=settings.cache_root)
        generated_test_run_service = GeneratedTestRunService(
            provider=model_provider,
            model=settings.test_generator_model,
            artifact_store=artifact_store,
            agent_trace_repo=agent_trace_repo,
            generated_test_repo=generated_test_repo,
        )

    return TestGenerationService(
        repository_service=repository_service,
        repository_repo=RepositoryRepo(db),
        repo_snapshot_repo=RepoSnapshotRepo(db),
        context_pack_repo=ContextPackRepo(db),
        generated_test_repo=GeneratedTestRepo(db),
        job_repo=JobRepo(db),
        generated_test_run_service=generated_test_run_service,
    )


def build_answer_judging_service(
    db: Database | None = None,
    *,
    settings: Settings | None = None,
    include_runner: bool = False,
) -> AnswerJudgingService:
    db = db or get_database()
    settings = settings or get_settings()
    answer_judging_run_service = None
    if include_runner:
        agent_trace_repo = AgentTraceRepo(db)
        test_result_repo = TestResultRepo(db)
        saved_learning_repo = SavedLearningRepo(db)
        model_provider = build_model_provider(settings)
        artifact_store = AgentArtifactStore(cache_root=settings.cache_root)
        answer_judging_run_service = AnswerJudgingRunService(
            provider=model_provider,
            model=settings.answer_judge_model,
            artifact_store=artifact_store,
            agent_trace_repo=agent_trace_repo,
            test_result_repo=test_result_repo,
            saved_learning_repo=saved_learning_repo,
        )

    return AnswerJudgingService(
        generated_test_repo=GeneratedTestRepo(db),
        test_answer_repo=TestAnswerRepo(db),
        test_result_repo=TestResultRepo(db),
        repository_repo=RepositoryRepo(db),
        job_repo=JobRepo(db),
        answer_judge_model=settings.answer_judge_model,
        answer_judging_run_service=answer_judging_run_service,
    )


def build_learning_service(db: Database | None = None) -> LearningService:
    db = db or get_database()
    return LearningService(
        repository_repo=RepositoryRepo(db),
        saved_learning_repo=SavedLearningRepo(db),
    )


def build_worker_services(db: Database, *, settings: Settings | None = None) -> WorkerServices:
    settings = settings or get_settings()
    repository_service = build_repository_service(
        db,
        settings=settings,
        include_worker_adapters=True,
    )
    return WorkerServices(
        jobs=build_job_service(db),
        repositories=repository_service,
        test_generation=build_test_generation_service(
            db,
            settings=settings,
            repository_service=repository_service,
            include_runner=True,
        ),
        answer_judging=build_answer_judging_service(
            db,
            settings=settings,
            include_runner=True,
        ),
    )
