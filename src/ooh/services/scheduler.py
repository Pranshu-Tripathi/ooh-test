import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ooh.db.models import ContextPackType, JobStatus, JobType, RepositoryScheduleRead
from ooh.db.repos import JobRepo, RepositoryRepo, RepositoryScheduleRepo
from ooh.generation_config import (
    MAX_GENERATION_QUESTIONS_PER_CATEGORY,
    MAX_GENERATION_QUESTIONS_PER_JOB,
    validate_default_generation_questions_per_category,
)
from ooh.services.exceptions import NotFoundError
from ooh.scheduler.repository_change_detector import GitRepositoryChangeDetector

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepositoryPollResult:
    checked_count: int
    unchanged_count: int
    changed_count: int
    enqueued_count: int
    active_count: int
    failed_count: int


@dataclass(frozen=True)
class SchedulerTickResult:
    repository_poll: RepositoryPollResult | None
    evaluated_count: int
    triggered_count: int


class SchedulerService:
    def __init__(
        self,
        *,
        repository_repo: RepositoryRepo,
        repository_schedule_repo: RepositoryScheduleRepo,
        job_repo: JobRepo | None = None,
        repository_change_detector: GitRepositoryChangeDetector | None = None,
        questions_per_category_default: int,
    ) -> None:
        self.repository_repo = repository_repo
        self.repository_schedule_repo = repository_schedule_repo
        self.job_repo = job_repo
        self.repository_change_detector = repository_change_detector
        self.questions_per_category_default = validate_default_generation_questions_per_category(
            questions_per_category_default
        )

    def get_repository_schedule(self, repository_id: UUID) -> RepositoryScheduleRead | None:
        self._ensure_repository_exists(repository_id)
        return self.repository_schedule_repo.get_for_repository(repository_id)

    def update_repository_schedule(
        self,
        *,
        repository_id: UUID,
        enabled: bool,
        drift_min_score: Decimal,
        drift_max_score: Decimal | None,
        pack_types: list[ContextPackType] | None,
        question_counts: dict[ContextPackType, int] | None,
        max_questions_per_trigger: int,
    ) -> RepositoryScheduleRead:
        self._ensure_repository_exists(repository_id)
        if drift_min_score < 0:
            raise ValueError("drift_min_score cannot be negative")
        if drift_max_score is not None and drift_max_score < drift_min_score:
            raise ValueError("drift_max_score cannot be lower than drift_min_score")
        if question_counts is None:
            pack_types = pack_types or [ContextPackType.LOW_LEVEL_COMPONENTS]
            question_counts = {
                pack_type: self.questions_per_category_default for pack_type in pack_types
            }
        if not question_counts:
            raise ValueError("at least one context pack type is required")
        if any(
            question_count < 1 or question_count > MAX_GENERATION_QUESTIONS_PER_CATEGORY
            for question_count in question_counts.values()
        ):
            raise ValueError(
                f"question counts must be between 1 and {MAX_GENERATION_QUESTIONS_PER_CATEGORY}"
            )
        if (
            max_questions_per_trigger < 1
            or max_questions_per_trigger > MAX_GENERATION_QUESTIONS_PER_JOB
        ):
            raise ValueError(
                f"max_questions_per_trigger must be between 1 and "
                f"{MAX_GENERATION_QUESTIONS_PER_JOB}"
            )
        if sum(question_counts.values()) > max_questions_per_trigger:
            raise ValueError("generation plan cannot exceed max_questions_per_trigger")
        return self.repository_schedule_repo.upsert(
            repository_id=repository_id,
            enabled=enabled,
            drift_min_score=drift_min_score,
            drift_max_score=drift_max_score,
            generation_plan=[
                {
                    "category": pack_type.value,
                    "question_count": question_count,
                }
                for pack_type, question_count in question_counts.items()
            ],
            max_questions_per_trigger=max_questions_per_trigger,
        )

    def tick(
        self,
        *,
        batch_size: int,
        poll_repositories: bool = False,
    ) -> SchedulerTickResult:
        repository_poll = self.poll_repositories() if poll_repositories else None
        evaluations = self.repository_schedule_repo.evaluate_pending(
            limit=batch_size,
            questions_per_category=self.questions_per_category_default,
        )
        return SchedulerTickResult(
            repository_poll=repository_poll,
            evaluated_count=len(evaluations),
            triggered_count=sum(1 for evaluation in evaluations if evaluation.matched),
        )

    def poll_repositories(self) -> RepositoryPollResult:
        if self.job_repo is None or self.repository_change_detector is None:
            raise RuntimeError("repository polling dependencies are not configured")

        repositories = self.repository_repo.list_all()
        unchanged_count = 0
        changed_count = 0
        enqueued_count = 0
        active_count = 0
        failed_count = 0

        for repository in repositories:
            if self.job_repo.has_active_repository_job(
                repository_id=repository.id,
                job_type=JobType.INGEST_REPOSITORY,
            ):
                active_count += 1
                logger.debug(
                    "repository poll skipped active ingestion repository_id=%s",
                    repository.id,
                )
                continue

            try:
                head = self.repository_change_detector.detect(repository)
                if head.commit_sha == repository.last_processed_commit_sha:
                    unchanged_count += 1
                    logger.debug(
                        "repository unchanged repository_id=%s commit_sha=%s",
                        repository.id,
                        head.commit_sha,
                    )
                    continue

                changed_count += 1
                logger.info(
                    "repository change detected repository_id=%s from_commit_sha=%s "
                    "to_commit_sha=%s ref=%s",
                    repository.id,
                    repository.last_processed_commit_sha,
                    head.commit_sha,
                    head.ref,
                )
                from_commit_sha = repository.last_processed_commit_sha or "baseline"
                job = self.job_repo.enqueue(
                    repository_id=repository.id,
                    job_type=JobType.INGEST_REPOSITORY,
                    payload={
                        "repository_id": str(repository.id),
                        "source_type": repository.source_type.value,
                        "source_uri": repository.source_uri,
                        "target_commit_sha": head.commit_sha,
                        "trigger": {
                            "type": "repository_poll",
                            "ref": head.ref,
                            "from_commit_sha": repository.last_processed_commit_sha,
                        },
                    },
                    idempotency_key=(
                        f"repository-ingest:{repository.id}:"
                        f"{from_commit_sha}:{head.commit_sha}"
                    ),
                )
                if job.status in {
                    JobStatus.QUEUED,
                    JobStatus.RUNNING,
                    JobStatus.RETRY_WAIT,
                }:
                    enqueued_count += 1
                    logger.info(
                        "repository ingestion queued repository_id=%s job_id=%s "
                        "target_commit_sha=%s",
                        repository.id,
                        job.id,
                        head.commit_sha,
                    )
                else:
                    failed_count += 1
                    logger.warning(
                        "repository ingestion not queued repository_id=%s job_id=%s "
                        "target_commit_sha=%s existing_status=%s",
                        repository.id,
                        job.id,
                        head.commit_sha,
                        job.status.value,
                    )
            except Exception:
                failed_count += 1
                logger.exception(
                    "repository poll failed repository_id=%s source_type=%s",
                    repository.id,
                    repository.source_type.value,
                )

        return RepositoryPollResult(
            checked_count=len(repositories),
            unchanged_count=unchanged_count,
            changed_count=changed_count,
            enqueued_count=enqueued_count,
            active_count=active_count,
            failed_count=failed_count,
        )

    def _ensure_repository_exists(self, repository_id: UUID) -> None:
        if self.repository_repo.get(repository_id) is None:
            raise NotFoundError("repository not found")
