from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select

from ooh.db.connection import Database
from ooh.db.models import SavedLearning, SavedLearningRead


@dataclass(frozen=True)
class SavedLearningInput:
    repository_id: UUID
    test_result_id: UUID | None
    title: str
    summary: str
    source_payload: dict[str, Any]


class SavedLearningRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, input: SavedLearningInput) -> SavedLearningRead:
        with self.db.session() as session:
            saved_learning = SavedLearning(
                repository_id=input.repository_id,
                test_result_id=input.test_result_id,
                title=input.title,
                summary=input.summary,
                source_payload=input.source_payload,
            )
            session.add(saved_learning)
            session.flush()
            session.refresh(saved_learning)
            return SavedLearningRead.model_validate(saved_learning)

    def list_for_repository(self, repository_id: UUID, *, limit: int = 50) -> list[SavedLearningRead]:
        with self.db.session() as session:
            saved_learnings = session.scalars(
                select(SavedLearning)
                .where(SavedLearning.repository_id == repository_id)
                .order_by(SavedLearning.created_at.desc())
                .limit(limit)
            ).all()
            return [SavedLearningRead.model_validate(saved_learning) for saved_learning in saved_learnings]
