from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select

from ooh.db.connection import Database
from ooh.db.models import GeneratedTest, TestResult, TestResultRead, TestResultStatus


@dataclass(frozen=True)
class TestResultInput:
    generated_test_id: UUID
    test_answer_id: UUID | None
    agent_run_id: UUID | None
    score: Decimal
    status: TestResultStatus
    feedback: dict[str, Any]
    alert_flag: bool


class TestResultRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, input: TestResultInput) -> TestResultRead:
        with self.db.session() as session:
            result = TestResult(
                generated_test_id=input.generated_test_id,
                test_answer_id=input.test_answer_id,
                agent_run_id=input.agent_run_id,
                score=input.score,
                status=input.status,
                feedback=input.feedback,
                alert_flag=input.alert_flag,
            )
            session.add(result)
            session.flush()
            session.refresh(result)
            return TestResultRead.model_validate(result)

    def list_for_generated_test(self, generated_test_id: UUID) -> list[TestResultRead]:
        with self.db.session() as session:
            results = session.scalars(
                select(TestResult)
                .where(TestResult.generated_test_id == generated_test_id)
                .order_by(TestResult.created_at.desc())
            ).all()
            return [TestResultRead.model_validate(result) for result in results]

    def list_for_repository(self, repository_id: UUID, *, limit: int = 50) -> list[TestResultRead]:
        with self.db.session() as session:
            results = session.scalars(
                select(TestResult)
                .join(GeneratedTest, GeneratedTest.id == TestResult.generated_test_id)
                .where(GeneratedTest.repository_id == repository_id)
                .order_by(TestResult.created_at.desc())
                .limit(limit)
            ).all()
            return [TestResultRead.model_validate(result) for result in results]
