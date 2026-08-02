from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select

from ooh.db.connection import Database
from ooh.db.models import TestAnswer, TestAnswerRead


@dataclass(frozen=True)
class TestAnswerInput:
    generated_test_id: UUID
    answer_payload: dict[str, Any]


class TestAnswerRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, input: TestAnswerInput) -> TestAnswerRead:
        with self.db.session() as session:
            test_answer = TestAnswer(
                generated_test_id=input.generated_test_id,
                answer_payload=input.answer_payload,
            )
            session.add(test_answer)
            session.flush()
            session.refresh(test_answer)
            return TestAnswerRead.model_validate(test_answer)

    def get(self, test_answer_id: UUID) -> TestAnswerRead | None:
        with self.db.session() as session:
            test_answer = session.get(TestAnswer, test_answer_id)
            if test_answer is None:
                return None
            return TestAnswerRead.model_validate(test_answer)

    def list_for_generated_test(self, generated_test_id: UUID) -> list[TestAnswerRead]:
        with self.db.session() as session:
            answers = session.scalars(
                select(TestAnswer)
                .where(TestAnswer.generated_test_id == generated_test_id)
                .order_by(TestAnswer.submitted_at.desc())
            ).all()
            return [TestAnswerRead.model_validate(answer) for answer in answers]
