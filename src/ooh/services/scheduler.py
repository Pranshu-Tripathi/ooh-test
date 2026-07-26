from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ooh.db.models import ContextPackType, RepositoryScheduleRead
from ooh.db.repos import RepositoryRepo, RepositoryScheduleRepo
from ooh.generation_config import (
    MAX_GENERATION_QUESTIONS_PER_CATEGORY,
    MAX_GENERATION_QUESTIONS_PER_JOB,
    validate_default_generation_questions_per_category,
)
from ooh.services.exceptions import NotFoundError


@dataclass(frozen=True)
class SchedulerTickResult:
    evaluated_count: int
    triggered_count: int


class SchedulerService:
    def __init__(
        self,
        *,
        repository_repo: RepositoryRepo,
        repository_schedule_repo: RepositoryScheduleRepo,
        questions_per_category_default: int,
    ) -> None:
        self.repository_repo = repository_repo
        self.repository_schedule_repo = repository_schedule_repo
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

    def tick(self, *, batch_size: int) -> SchedulerTickResult:
        evaluations = self.repository_schedule_repo.evaluate_pending(
            limit=batch_size,
            questions_per_category=self.questions_per_category_default,
        )
        return SchedulerTickResult(
            evaluated_count=len(evaluations),
            triggered_count=sum(1 for evaluation in evaluations if evaluation.matched),
        )

    def _ensure_repository_exists(self, repository_id: UUID) -> None:
        if self.repository_repo.get(repository_id) is None:
            raise NotFoundError("repository not found")
