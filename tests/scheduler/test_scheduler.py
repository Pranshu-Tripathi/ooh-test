from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ooh.api.schemas.repositories import RepositoryScheduleUpdateRequest
from ooh.db.models import ContextPackType, JobType
from ooh.db.repos.repository_schedules import RepositoryScheduleRepo
from ooh.services.scheduler import SchedulerService
from ooh.worker.runner import JobRunner


@pytest.mark.parametrize(
    ("score", "minimum", "maximum", "expected"),
    [
        ("24.99", "25", None, False),
        ("25", "25", None, True),
        ("80", "25", "80", True),
        ("80.01", "25", "80", False),
    ],
)
def test_drift_score_range_is_inclusive(
    score: str,
    minimum: str,
    maximum: str | None,
    expected: bool,
) -> None:
    assert (
        RepositoryScheduleRepo.score_matches(
            Decimal(score),
            minimum=Decimal(minimum),
            maximum=Decimal(maximum) if maximum is not None else None,
        )
        is expected
    )


def test_schedule_request_rejects_reversed_or_duplicate_settings() -> None:
    with pytest.raises(ValidationError, match="greater than or equal"):
        RepositoryScheduleUpdateRequest(
            drift_min_score="80",
            drift_max_score="25",
        )

    with pytest.raises(ValidationError, match="must not contain duplicates"):
        RepositoryScheduleUpdateRequest(
            pack_types=[
                ContextPackType.ACTIVE_PR,
                ContextPackType.ACTIVE_PR,
            ]
        )


def test_scheduler_tick_reports_evaluated_and_triggered_counts() -> None:
    schedule_repo = FakeScheduleRepo()
    schedule_repo.evaluations = [
        SimpleNamespace(matched=False),
        SimpleNamespace(matched=True),
        SimpleNamespace(matched=True),
    ]
    service = SchedulerService(
        repository_repo=FakeRepositoryRepo(),
        repository_schedule_repo=schedule_repo,
        questions_per_category_default=3,
    )

    result = service.tick(batch_size=25)

    assert schedule_repo.batch_size == 25
    assert schedule_repo.questions_per_category == 3
    assert result.evaluated_count == 3
    assert result.triggered_count == 2


def test_worker_job_type_filter_supports_kubernetes_role_splitting() -> None:
    assert JobRunner._configured_job_types(None) is None
    assert JobRunner._configured_job_types("  ") is None
    assert JobRunner._configured_job_types("ingest_repository, compute_drift") == {
        JobType.INGEST_REPOSITORY,
        JobType.COMPUTE_DRIFT,
    }

    with pytest.raises(ValueError):
        JobRunner._configured_job_types("not_a_job")


class FakeRepositoryRepo:
    def get(self, _repository_id: object) -> object:
        return SimpleNamespace(id=uuid4())


class FakeScheduleRepo:
    def __init__(self) -> None:
        self.evaluations: list[object] = []
        self.batch_size: int | None = None
        self.questions_per_category: int | None = None

    def evaluate_pending(
        self,
        *,
        limit: int,
        questions_per_category: int,
    ) -> list[object]:
        self.batch_size = limit
        self.questions_per_category = questions_per_category
        return self.evaluations
