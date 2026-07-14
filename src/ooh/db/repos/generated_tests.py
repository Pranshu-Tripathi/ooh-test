from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select

from ooh.db.connection import Database
from ooh.db.models import GeneratedTest, GeneratedTestCategory, GeneratedTestRead


@dataclass(frozen=True)
class GeneratedTestInput:
    repository_id: UUID
    snapshot_id: UUID
    drift_event_id: UUID | None
    agent_run_id: UUID | None
    context_pack_id: UUID | None
    category: GeneratedTestCategory
    test_payload: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    prompt_version: str | None


class GeneratedTestRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, generated_test_id: UUID) -> GeneratedTestRead | None:
        with self.db.session() as session:
            generated_test = session.get(GeneratedTest, generated_test_id)
            if generated_test is None:
                return None
            return GeneratedTestRead.model_validate(generated_test)

    def create_many(self, inputs: list[GeneratedTestInput]) -> list[GeneratedTestRead]:
        if not inputs:
            return []

        with self.db.session() as session:
            generated_tests = [
                GeneratedTest(
                    repository_id=input.repository_id,
                    snapshot_id=input.snapshot_id,
                    drift_event_id=input.drift_event_id,
                    agent_run_id=input.agent_run_id,
                    context_pack_id=input.context_pack_id,
                    category=input.category,
                    test_payload=input.test_payload,
                    evidence_refs=input.evidence_refs,
                    prompt_version=input.prompt_version,
                )
                for input in inputs
            ]
            session.add_all(generated_tests)
            session.flush()
            for generated_test in generated_tests:
                session.refresh(generated_test)
            return [
                GeneratedTestRead.model_validate(generated_test)
                for generated_test in generated_tests
            ]

    def list_for_repository(
        self,
        repository_id: UUID,
        *,
        limit: int = 50,
    ) -> list[GeneratedTestRead]:
        with self.db.session() as session:
            generated_tests = session.scalars(
                select(GeneratedTest)
                .where(GeneratedTest.repository_id == repository_id)
                .order_by(GeneratedTest.created_at.desc())
                .limit(limit)
            ).all()
            return [
                GeneratedTestRead.model_validate(generated_test)
                for generated_test in generated_tests
            ]
