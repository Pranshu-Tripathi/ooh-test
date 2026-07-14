from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ooh.db.models import ContextPackType, JobRead, JobStatus, JobType
from ooh.worker.runner import JobRunner


def test_requested_pack_types_accepts_context_pack_values() -> None:
    job = build_job({"pack_types": ["low_level_components", "active_pr"]})

    assert JobRunner._requested_pack_types(job) == {
        ContextPackType.LOW_LEVEL_COMPONENTS,
        ContextPackType.ACTIVE_PR,
    }


def test_requested_pack_types_returns_none_when_unset_or_empty() -> None:
    assert JobRunner._requested_pack_types(build_job({})) is None
    assert JobRunner._requested_pack_types(build_job({"pack_types": []})) is None


def test_requested_pack_types_rejects_invalid_shape() -> None:
    with pytest.raises(ValueError, match="pack_types must be a list"):
        JobRunner._requested_pack_types(build_job({"pack_types": "active_pr"}))

    with pytest.raises(ValueError, match="pack_types must contain strings"):
        JobRunner._requested_pack_types(build_job({"pack_types": [1]}))


def build_job(payload: dict[str, object]) -> JobRead:
    now = datetime.now(UTC)
    return JobRead(
        id=uuid4(),
        repository_id=uuid4(),
        job_type=JobType.GENERATE_TEST,
        status=JobStatus.RUNNING,
        attempt_count=1,
        max_attempts=3,
        payload=payload,
        run_after=now,
        locked_by="worker",
        locked_at=now,
        error_summary=None,
        created_at=now,
        updated_at=now,
    )
