from uuid import UUID

from fastapi import APIRouter, status

from ooh.api.errors import raise_http_for_service_error
from ooh.api.schemas.tests import (
    JobResponse,
    SavedLearningResponse,
    TestAnswerResponse,
    TestAnswerSubmissionResponse,
    TestAnswerSubmitRequest,
    TestResultResponse,
)
from ooh.services import (
    ServiceError,
    build_answer_judging_service,
    build_learning_service,
)

router = APIRouter(tags=["tests"])
answer_judging_service = build_answer_judging_service()
learning_service = build_learning_service()


@router.post(
    "/generated-tests/{generated_test_id}/answers",
    response_model=TestAnswerSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_test_answer(
    generated_test_id: UUID,
    request: TestAnswerSubmitRequest,
) -> TestAnswerSubmissionResponse:
    try:
        submission = answer_judging_service.submit_answer(
            generated_test_id=generated_test_id,
            answer_payload=request.to_payload(),
        )
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return TestAnswerSubmissionResponse(
        answer=TestAnswerResponse.from_record(submission.answer),
        judge_job=JobResponse.from_record(submission.judge_job),
    )


@router.get(
    "/generated-tests/{generated_test_id}/answers",
    response_model=list[TestAnswerResponse],
)
def list_test_answers(generated_test_id: UUID) -> list[TestAnswerResponse]:
    try:
        answers = answer_judging_service.list_answers(generated_test_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [TestAnswerResponse.from_record(answer) for answer in answers]


@router.get(
    "/generated-tests/{generated_test_id}/results",
    response_model=list[TestResultResponse],
)
def list_test_results(generated_test_id: UUID) -> list[TestResultResponse]:
    try:
        results = answer_judging_service.list_results(generated_test_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [TestResultResponse.from_record(result) for result in results]


@router.get(
    "/repositories/{repository_id}/test-results",
    response_model=list[TestResultResponse],
)
def list_repository_test_results(repository_id: UUID) -> list[TestResultResponse]:
    try:
        results = answer_judging_service.list_repository_results(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [TestResultResponse.from_record(result) for result in results]


@router.get(
    "/repositories/{repository_id}/learnings",
    response_model=list[SavedLearningResponse],
)
def list_repository_learnings(repository_id: UUID) -> list[SavedLearningResponse]:
    try:
        learnings = learning_service.list_repository_learnings(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [SavedLearningResponse.from_record(learning) for learning in learnings]
