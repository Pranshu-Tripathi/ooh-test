from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ooh.agent.contracts import (
    generated_test_grading_payload,
    normalize_submitted_answer_payload,
)
from ooh.agent.answer_judging_runner import AnswerJudgingRunResult, AnswerJudgingRunService
from ooh.db.models import GeneratedTestRead, JobRead, JobType, TestAnswerRead, TestResultRead
from ooh.db.repos import (
    GeneratedTestRepo,
    JobRepo,
    RepositoryRepo,
    TestAnswerInput,
    TestAnswerRepo,
    TestResultRepo,
)
from ooh.services.exceptions import ConflictError, NotFoundError


@dataclass(frozen=True)
class AnswerSubmission:
    answer: TestAnswerRead
    judge_job: JobRead


class AnswerJudgingService:
    def __init__(
        self,
        *,
        generated_test_repo: GeneratedTestRepo,
        test_answer_repo: TestAnswerRepo,
        test_result_repo: TestResultRepo,
        repository_repo: RepositoryRepo,
        job_repo: JobRepo,
        answer_judge_model: str,
        answer_judging_run_service: AnswerJudgingRunService | None = None,
    ) -> None:
        self.generated_test_repo = generated_test_repo
        self.test_answer_repo = test_answer_repo
        self.test_result_repo = test_result_repo
        self.repository_repo = repository_repo
        self.job_repo = job_repo
        self.answer_judge_model = answer_judge_model
        self.answer_judging_run_service = answer_judging_run_service

    def submit_answer(
        self,
        *,
        generated_test_id: UUID,
        answer_payload: dict[str, Any],
    ) -> AnswerSubmission:
        generated_test = self.generated_test_repo.get(generated_test_id)
        if generated_test is None:
            raise NotFoundError("generated test not found")

        try:
            normalized_answer_payload = normalize_submitted_answer_payload(
                answer_payload,
                generated_test_payload=generated_test.test_payload,
            )
        except ValueError as exc:
            raise ConflictError(f"answer does not match generated test: {exc}") from exc

        answer = self.test_answer_repo.create(
            TestAnswerInput(
                generated_test_id=generated_test_id,
                answer_payload=normalized_answer_payload,
            )
        )
        judge_job = self.job_repo.enqueue(
            repository_id=generated_test.repository_id,
            job_type=JobType.JUDGE_ANSWER,
            payload={
                "generated_test_id": str(generated_test_id),
                "test_answer_id": str(answer.id),
                "model": self.answer_judge_model,
            },
        )
        return AnswerSubmission(answer=answer, judge_job=judge_job)

    def list_answers(self, generated_test_id: UUID) -> list[TestAnswerRead]:
        self._get_generated_test(generated_test_id)
        return self.test_answer_repo.list_for_generated_test(generated_test_id)

    def list_results(self, generated_test_id: UUID) -> list[TestResultRead]:
        self._get_generated_test(generated_test_id)
        return self.test_result_repo.list_for_generated_test(generated_test_id)

    def get_grading_guidance(self, generated_test_id: UUID) -> dict[str, Any]:
        generated_test = self._get_generated_test(generated_test_id)
        return generated_test_grading_payload(generated_test.test_payload)

    def list_repository_results(self, repository_id: UUID) -> list[TestResultRead]:
        if self.repository_repo.get(repository_id) is None:
            raise NotFoundError("repository not found")
        return self.test_result_repo.list_for_repository(repository_id)

    def run_judge_answer_job(self, job: JobRead) -> AnswerJudgingRunResult:
        if self.answer_judging_run_service is None:
            raise RuntimeError("answer judging runner is not configured")

        generated_test_id = payload_uuid(job, "generated_test_id")
        test_answer_id = payload_uuid(job, "test_answer_id")

        generated_test = self.generated_test_repo.get(generated_test_id)
        if generated_test is None:
            raise ValueError(f"generated test not found: {generated_test_id}")

        test_answer = self.test_answer_repo.get(test_answer_id)
        if test_answer is None:
            raise ValueError(f"test answer not found: {test_answer_id}")
        if test_answer.generated_test_id != generated_test.id:
            raise ValueError("test answer does not belong to generated test")

        return self.answer_judging_run_service.judge_answer(
            generated_test=generated_test,
            test_answer=test_answer,
            job_id=job.id,
        )

    def _get_generated_test(self, generated_test_id: UUID) -> GeneratedTestRead:
        generated_test = self.generated_test_repo.get(generated_test_id)
        if generated_test is None:
            raise NotFoundError("generated test not found")
        return generated_test


def payload_uuid(job: JobRead, key: str) -> UUID:
    raw_value = job.payload.get(key)
    if not isinstance(raw_value, str):
        raise ValueError(f"{job.job_type.value} payload requires string {key}")
    return UUID(raw_value)
