from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, uuid_pk
from ooh.db.models.enums import TestResultStatus


class TestResult(Base):
    __tablename__ = "test_results"
    __table_args__ = (
        sa.CheckConstraint("score >= 0 and score <= 1", name="ck_test_results_score_range"),
        sa.Index("ix_test_results_generated_test", "generated_test_id"),
        sa.Index("ix_test_results_status", "status"),
    )

    id: Mapped[UUID] = uuid_pk()
    generated_test_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("generated_tests.id", ondelete="CASCADE"),
        nullable=False,
    )
    test_answer_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("test_answers.id", ondelete="SET NULL"),
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
    )
    score: Mapped[Decimal] = mapped_column(sa.Numeric(5, 4), nullable=False)
    status: Mapped[TestResultStatus] = enum_column(TestResultStatus, nullable=False)
    feedback: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    alert_flag: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    created_at: Mapped[datetime] = created_at_column()


class TestResultRead(OrmModel):
    id: UUID
    generated_test_id: UUID
    test_answer_id: UUID | None
    agent_run_id: UUID | None
    score: Decimal
    status: TestResultStatus
    feedback: dict[str, Any]
    alert_flag: bool
    created_at: datetime
