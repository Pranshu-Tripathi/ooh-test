from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ooh.api.schemas.repositories import RepositoryScheduleUpdateRequest
from ooh.db.models import (
    ContextPackType,
    JobStatus,
    JobType,
    RepositorySourceType,
)
from ooh.scheduler.repository_change_detector import RepositoryHead
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

    with pytest.raises(ValidationError, match="cannot exceed max_questions_per_trigger"):
        RepositoryScheduleUpdateRequest(
            generation_plan=[
                {"category": ContextPackType.ACTIVE_PR, "question_count": 3},
                {
                    "category": ContextPackType.LOW_LEVEL_COMPONENTS,
                    "question_count": 2,
                },
            ],
            max_questions_per_trigger=4,
        )


def test_repository_schedule_persists_the_generation_mix() -> None:
    schedule_repo = FakeScheduleRepo()
    service = SchedulerService(
        repository_repo=FakeRepositoryRepo(),
        repository_schedule_repo=schedule_repo,
        questions_per_category_default=2,
    )

    service.update_repository_schedule(
        repository_id=uuid4(),
        enabled=True,
        drift_min_score=Decimal("25"),
        drift_max_score=None,
        pack_types=None,
        question_counts={
            ContextPackType.LOW_LEVEL_COMPONENTS: 3,
            ContextPackType.ACTIVE_PR: 2,
        },
        max_questions_per_trigger=6,
    )

    assert schedule_repo.updated_generation_plan == [
        {"category": "low_level_components", "question_count": 3},
        {"category": "active_pr", "question_count": 2},
    ]
    assert schedule_repo.updated_question_limit == 6


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


def test_repository_poll_queues_one_exact_sha_ingestion_for_aggregate_change() -> None:
    repository_id = uuid4()
    repository_repo = FakeRepositoryRepo()
    repository_repo.repositories = [
        SimpleNamespace(
            id=repository_id,
            source_type=RepositorySourceType.LOCAL_PATH,
            source_uri="/workspace",
            last_processed_commit_sha="old-sha",
        )
    ]
    job_repo = FakeJobRepo()
    detector = FakeChangeDetector(
        heads={repository_id: RepositoryHead(commit_sha="new-sha", ref="HEAD")}
    )
    service = SchedulerService(
        repository_repo=repository_repo,
        repository_schedule_repo=FakeScheduleRepo(),
        job_repo=job_repo,
        repository_change_detector=detector,
        questions_per_category_default=3,
    )

    result = service.poll_repositories()

    assert result.checked_count == 1
    assert result.changed_count == 1
    assert result.enqueued_count == 1
    assert job_repo.enqueued[0]["idempotency_key"] == (
        f"repository-ingest:{repository_id}:old-sha:new-sha"
    )
    assert job_repo.enqueued[0]["payload"] == {
        "repository_id": str(repository_id),
        "source_type": "local_path",
        "source_uri": "/workspace",
        "target_commit_sha": "new-sha",
        "trigger": {
            "type": "repository_poll",
            "ref": "HEAD",
            "from_commit_sha": "old-sha",
        },
    }


def test_repository_poll_skips_unchanged_and_active_repositories() -> None:
    unchanged_id = uuid4()
    active_id = uuid4()
    repository_repo = FakeRepositoryRepo()
    repository_repo.repositories = [
        SimpleNamespace(
            id=unchanged_id,
            source_type=RepositorySourceType.GITHUB,
            source_uri="https://github.com/example/unchanged.git",
            last_processed_commit_sha="same-sha",
        ),
        SimpleNamespace(
            id=active_id,
            source_type=RepositorySourceType.LOCAL_PATH,
            source_uri="/workspace",
            last_processed_commit_sha="old-sha",
        ),
    ]
    detector = FakeChangeDetector(
        heads={unchanged_id: RepositoryHead(commit_sha="same-sha", ref="HEAD")}
    )
    job_repo = FakeJobRepo(active_repository_ids={active_id})
    service = SchedulerService(
        repository_repo=repository_repo,
        repository_schedule_repo=FakeScheduleRepo(),
        job_repo=job_repo,
        repository_change_detector=detector,
        questions_per_category_default=3,
    )

    result = service.poll_repositories()

    assert result.checked_count == 2
    assert result.unchanged_count == 1
    assert result.active_count == 1
    assert result.changed_count == 0
    assert result.enqueued_count == 0
    assert detector.detected_repository_ids == [unchanged_id]
    assert job_repo.enqueued == []


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
    def __init__(self) -> None:
        self.repositories: list[object] = []

    def get(self, _repository_id: object) -> object:
        return SimpleNamespace(id=uuid4())

    def list_all(self) -> list[object]:
        return self.repositories


class FakeJobRepo:
    def __init__(self, *, active_repository_ids: set[object] | None = None) -> None:
        self.active_repository_ids = active_repository_ids or set()
        self.enqueued: list[dict[str, object]] = []

    def has_active_repository_job(
        self,
        *,
        repository_id: object,
        job_type: JobType,
    ) -> bool:
        assert job_type == JobType.INGEST_REPOSITORY
        return repository_id in self.active_repository_ids

    def enqueue(self, **kwargs: object) -> object:
        self.enqueued.append(kwargs)
        return SimpleNamespace(id=uuid4(), status=JobStatus.QUEUED)


class FakeChangeDetector:
    def __init__(self, *, heads: dict[object, RepositoryHead]) -> None:
        self.heads = heads
        self.detected_repository_ids: list[object] = []

    def detect(self, repository: object) -> RepositoryHead:
        repository_id = repository.id
        self.detected_repository_ids.append(repository_id)
        return self.heads[repository_id]


class FakeScheduleRepo:
    def __init__(self) -> None:
        self.evaluations: list[object] = []
        self.batch_size: int | None = None
        self.questions_per_category: int | None = None
        self.updated_generation_plan: list[dict[str, object]] | None = None
        self.updated_question_limit: int | None = None

    def upsert(
        self,
        *,
        generation_plan: list[dict[str, object]],
        max_questions_per_trigger: int,
        **_kwargs: object,
    ) -> object:
        self.updated_generation_plan = generation_plan
        self.updated_question_limit = max_questions_per_trigger
        return SimpleNamespace()

    def evaluate_pending(
        self,
        *,
        limit: int,
        questions_per_category: int,
    ) -> list[object]:
        self.batch_size = limit
        self.questions_per_category = questions_per_category
        return self.evaluations
