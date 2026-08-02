from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, updated_at_column, uuid_pk
from ooh.db.models.enums import ContextPackType


class RepositorySchedule(Base):
    __tablename__ = "repository_schedules"
    __table_args__ = (
        sa.CheckConstraint("drift_min_score >= 0", name="ck_repository_schedules_min_score"),
        sa.CheckConstraint(
            "drift_max_score is null or drift_max_score >= drift_min_score",
            name="ck_repository_schedules_score_range",
        ),
        sa.CheckConstraint(
            "max_questions_per_trigger > 0 and max_questions_per_trigger <= 15",
            name="ck_repository_schedules_question_limit",
        ),
        sa.UniqueConstraint("repository_id", name="uq_repository_schedules_repository"),
        sa.Index("ix_repository_schedules_enabled", "enabled", "active_since"),
    )

    id: Mapped[UUID] = uuid_pk()
    repository_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    drift_min_score: Mapped[Decimal] = mapped_column(
        sa.Numeric(10, 2),
        nullable=False,
        server_default="25",
    )
    drift_max_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    pack_types: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'[\"low_level_components\"]'::jsonb"),
    )
    generation_plan: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text(
            """'[{"category": "low_level_components", "question_count": 1}]'::jsonb"""
        ),
    )
    max_questions_per_trigger: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="15",
    )
    active_since: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class RepositoryScheduleRead(OrmModel):
    id: UUID
    repository_id: UUID
    enabled: bool
    drift_min_score: Decimal
    drift_max_score: Decimal | None
    pack_types: list[ContextPackType]
    generation_plan: list[dict[str, Any]]
    max_questions_per_trigger: int
    active_since: datetime
    created_at: datetime
    updated_at: datetime


class DriftTriggerEvaluation(Base):
    __tablename__ = "drift_trigger_evaluations"
    __table_args__ = (
        sa.UniqueConstraint(
            "repository_schedule_id",
            "drift_event_id",
            name="uq_drift_trigger_evaluations_schedule_event",
        ),
        sa.Index(
            "ix_drift_trigger_evaluations_schedule_created",
            "repository_schedule_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    repository_schedule_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repository_schedules.id", ondelete="CASCADE"),
        nullable=False,
    )
    drift_event_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("drift_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    matched: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="SET NULL"),
    )
    evaluation: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = created_at_column()


class DriftTriggerEvaluationRead(OrmModel):
    id: UUID
    repository_schedule_id: UUID
    drift_event_id: UUID
    matched: bool
    job_id: UUID | None
    evaluation: dict[str, Any]
    created_at: datetime
