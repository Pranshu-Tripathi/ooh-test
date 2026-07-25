from types import SimpleNamespace
from uuid import uuid4

import pytest

from ooh.services.answer_judging import AnswerJudgingService
from ooh.services.exceptions import ConflictError


class RecordingAnswerRepo:
    def __init__(self) -> None:
        self.inputs = []

    def create(self, input):
        self.inputs.append(input)
        return SimpleNamespace(id=uuid4())


class RecordingJobRepo:
    def __init__(self) -> None:
        self.payloads = []

    def enqueue(self, **payload):
        self.payloads.append(payload)
        return SimpleNamespace(id=uuid4())


def build_service(test_payload):
    generated_test_id = uuid4()
    generated_test = SimpleNamespace(
        id=generated_test_id,
        repository_id=uuid4(),
        test_payload=test_payload,
    )
    answer_repo = RecordingAnswerRepo()
    job_repo = RecordingJobRepo()
    service = AnswerJudgingService(
        generated_test_repo=SimpleNamespace(get=lambda requested_id: generated_test),
        test_answer_repo=answer_repo,
        test_result_repo=SimpleNamespace(),
        repository_repo=SimpleNamespace(),
        job_repo=job_repo,
        answer_judge_model="judge",
    )
    return service, generated_test_id, answer_repo, job_repo


def test_submit_answer_normalizes_structured_payload_before_persistence() -> None:
    service, generated_test_id, answer_repo, job_repo = build_service(
        {
            "type": "mcq_single",
            "question": "Choose one.",
            "options": [{"id": "A", "text": "One"}, {"id": "B", "text": "Two"}],
            "correct_option_ids": ["A"],
        }
    )

    service.submit_answer(
        generated_test_id=generated_test_id,
        answer_payload={"type": "mcq_single", "selected_option_id": " A "},
    )

    assert answer_repo.inputs[0].answer_payload == {
        "type": "mcq_single",
        "selected_option_id": "A",
        "metadata": {},
    }
    assert job_repo.payloads[0]["payload"]["generated_test_id"] == str(generated_test_id)


def test_submit_answer_rejects_an_option_outside_the_generated_test() -> None:
    service, generated_test_id, answer_repo, job_repo = build_service(
        {
            "type": "mcq_single",
            "question": "Choose one.",
            "options": [{"id": "A", "text": "One"}, {"id": "B", "text": "Two"}],
            "correct_option_ids": ["A"],
        }
    )

    with pytest.raises(ConflictError, match="selected option ids are not present"):
        service.submit_answer(
            generated_test_id=generated_test_id,
            answer_payload={"type": "mcq_single", "selected_option_id": "C"},
        )

    assert answer_repo.inputs == []
    assert job_repo.payloads == []
