from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, uuid_pk


class TestAnswer(Base):
    __tablename__ = "test_answers"
    __table_args__ = (sa.Index("ix_test_answers_generated_test", "generated_test_id"),)

    id: Mapped[UUID] = uuid_pk()
    generated_test_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("generated_tests.id", ondelete="CASCADE"),
        nullable=False,
    )
    answer_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


class TestAnswerRead(OrmModel):
    id: UUID
    generated_test_id: UUID
    answer_payload: dict[str, Any]
    submitted_at: datetime
