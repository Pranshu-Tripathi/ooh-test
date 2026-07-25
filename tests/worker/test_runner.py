from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ooh.db.models import AgentStatus, ContextPackType, JobRead, JobStatus, JobType
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


def test_process_once_marks_succeeded_with_generation_result_metadata() -> None:
    job = build_job({"pack_types": ["low_level_components"]})
    generated_test_id = uuid4()
    agent_run_id = uuid4()
    context_pack_id = uuid4()
    runner = build_runner(
        job_service=FakeJobService(job),
        test_generation_service=SimpleNamespace(
            run_generation_job=lambda _job: SimpleNamespace(
                run_result=SimpleNamespace(
                    agent_run=SimpleNamespace(id=agent_run_id, status=AgentStatus.SUCCEEDED),
                    generated_tests=[SimpleNamespace(id=generated_test_id)],
                ),
                selected_context_packs=[
                    SimpleNamespace(
                        context_pack=SimpleNamespace(
                            id=context_pack_id,
                            pack_type=ContextPackType.LOW_LEVEL_COMPONENTS,
                        )
                    )
                ],
            )
        ),
    )

    assert runner.process_once() is True

    assert runner.job_service.succeeded_metadata == {
        "repository_id": str(job.repository_id),
        "agent_run_id": str(agent_run_id),
        "agent_run_status": "succeeded",
        "generated_test_count": 1,
        "generated_test_ids": [str(generated_test_id)],
        "context_pack_ids": [str(context_pack_id)],
        "context_pack_types": ["low_level_components"],
    }


def test_process_once_marks_failed_with_failure_result_metadata() -> None:
    job = build_job({"pack_types": ["low_level_components"]})

    def fail_generation(_job: JobRead) -> object:
        raise RuntimeError("model output did not match contract")

    runner = build_runner(
        job_service=FakeJobService(job),
        test_generation_service=SimpleNamespace(run_generation_job=fail_generation),
    )

    assert runner.process_once() is True

    assert runner.job_service.failed_metadata == {
        "repository_id": str(job.repository_id),
        "job_type": "generate_test",
        "attempt_count": 1,
        "max_attempts": 3,
        "worker_id": "test-worker",
        "error_type": "RuntimeError",
        "public_error": "generate_test failed (RuntimeError)",
    }


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
        result_metadata={},
        run_after=now,
        locked_by="worker",
        locked_at=now,
        error_summary=None,
        created_at=now,
        updated_at=now,
    )


def build_runner(
    *,
    job_service: "FakeJobService",
    test_generation_service: object | None = None,
) -> JobRunner:
    runner = JobRunner.__new__(JobRunner)
    runner.worker_id = "test-worker"
    runner.lease_duration = timedelta(minutes=30)
    runner.job_types = None
    runner.job_service = job_service
    runner.repository_service = SimpleNamespace(mark_repository_failed=lambda _repository_id: None)
    runner.test_generation_service = test_generation_service or SimpleNamespace()
    runner.answer_judging_service = SimpleNamespace()
    return runner


class FakeJobService:
    def __init__(self, job: JobRead) -> None:
        self.job = job
        self.succeeded_metadata: dict[str, object] | None = None
        self.failed_metadata: dict[str, object] | None = None

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
        job_types: set[JobType] | None,
    ) -> JobRead:
        assert worker_id == "test-worker"
        assert lease_duration == timedelta(minutes=30)
        assert job_types is None
        return self.job

    def mark_succeeded(
        self,
        job_id: object,
        *,
        result_metadata: dict[str, object] | None = None,
        expected_worker_id: str | None = None,
        expected_attempt_count: int | None = None,
    ) -> JobRead:
        assert job_id == self.job.id
        assert expected_worker_id == "test-worker"
        assert expected_attempt_count == self.job.attempt_count
        self.succeeded_metadata = result_metadata
        return self.job.model_copy(update={"status": JobStatus.SUCCEEDED})

    def mark_failed(
        self,
        job_id: object,
        *,
        error_summary: str,
        result_metadata: dict[str, object] | None = None,
        expected_worker_id: str | None = None,
        expected_attempt_count: int | None = None,
    ) -> JobRead:
        assert job_id == self.job.id
        assert error_summary == "generate_test failed (RuntimeError)"
        assert expected_worker_id == "test-worker"
        assert expected_attempt_count == self.job.attempt_count
        self.failed_metadata = result_metadata
        return self.job.model_copy(update={"status": JobStatus.RETRY_WAIT})
