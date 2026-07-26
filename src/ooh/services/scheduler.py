from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ooh.db.models import ContextPackType, RepositoryScheduleRead
from ooh.db.repos import RepositoryRepo, RepositoryScheduleRepo
from ooh.generation_config import validate_default_generation_questions_per_category
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
        self.questions_per_category_default = (
            validate_default_generation_questions_per_category(
                questions_per_category_default
            )
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
        pack_types: list[ContextPackType],
    ) -> RepositoryScheduleRead:
        self._ensure_repository_exists(repository_id)
        if drift_min_score < 0:
            raise ValueError("drift_min_score cannot be negative")
        if drift_max_score is not None and drift_max_score < drift_min_score:
            raise ValueError("drift_max_score cannot be lower than drift_min_score")
        if not pack_types:
            raise ValueError("at least one context pack type is required")
        return self.repository_schedule_repo.upsert(
            repository_id=repository_id,
            enabled=enabled,
            drift_min_score=drift_min_score,
            drift_max_score=drift_max_score,
            pack_types=pack_types,
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
