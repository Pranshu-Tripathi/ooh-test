from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from ooh.api.schemas.tests import (
    JobResponse,
    SavedLearningResponse,
    TestAnswerResponse,
    TestAnswerSubmissionResponse,
    TestAnswerSubmitRequest,
    TestResultResponse,
)
from ooh.config import get_settings
from ooh.db import get_database
from ooh.db.models import JobType
from ooh.db.repos import (
    GeneratedTestRepo,
    JobRepo,
    RepositoryRepo,
    SavedLearningRepo,
    TestAnswerInput,
    TestAnswerRepo,
    TestResultRepo,
)

router = APIRouter(tags=["tests"])
settings = get_settings()
generated_test_repo = GeneratedTestRepo(get_database())
test_answer_repo = TestAnswerRepo(get_database())
test_result_repo = TestResultRepo(get_database())
saved_learning_repo = SavedLearningRepo(get_database())
repository_repo = RepositoryRepo(get_database())
job_repo = JobRepo(get_database())


@router.post(
    "/generated-tests/{generated_test_id}/answers",
    response_model=TestAnswerSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_test_answer(
    generated_test_id: UUID,
    request: TestAnswerSubmitRequest,
) -> TestAnswerSubmissionResponse:
    generated_test = generated_test_repo.get(generated_test_id)
    if generated_test is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="generated test not found")

    answer = test_answer_repo.create(
        TestAnswerInput(
            generated_test_id=generated_test_id,
            answer_payload=request.to_payload(),
        )
    )
    judge_job = job_repo.enqueue(
        repository_id=generated_test.repository_id,
        job_type=JobType.JUDGE_ANSWER,
        payload={
            "generated_test_id": str(generated_test_id),
            "test_answer_id": str(answer.id),
            "model": settings.answer_judge_model,
        },
    )
    return TestAnswerSubmissionResponse(
        answer=TestAnswerResponse.from_record(answer),
        judge_job=JobResponse.from_record(judge_job),
    )


@router.get(
    "/generated-tests/{generated_test_id}/answers",
    response_model=list[TestAnswerResponse],
)
def list_test_answers(generated_test_id: UUID) -> list[TestAnswerResponse]:
    generated_test = generated_test_repo.get(generated_test_id)
    if generated_test is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="generated test not found")

    answers = test_answer_repo.list_for_generated_test(generated_test_id)
    return [TestAnswerResponse.from_record(answer) for answer in answers]


@router.get(
    "/generated-tests/{generated_test_id}/results",
    response_model=list[TestResultResponse],
)
def list_test_results(generated_test_id: UUID) -> list[TestResultResponse]:
    generated_test = generated_test_repo.get(generated_test_id)
    if generated_test is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="generated test not found")

    results = test_result_repo.list_for_generated_test(generated_test_id)
    return [TestResultResponse.from_record(result) for result in results]


@router.get(
    "/repositories/{repository_id}/test-results",
    response_model=list[TestResultResponse],
)
def list_repository_test_results(repository_id: UUID) -> list[TestResultResponse]:
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")

    results = test_result_repo.list_for_repository(repository_id)
    return [TestResultResponse.from_record(result) for result in results]


@router.get(
    "/repositories/{repository_id}/learnings",
    response_model=list[SavedLearningResponse],
)
def list_repository_learnings(repository_id: UUID) -> list[SavedLearningResponse]:
    repository = repository_repo.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="repository not found")

    learnings = saved_learning_repo.list_for_repository(repository_id)
    return [SavedLearningResponse.from_record(learning) for learning in learnings]
