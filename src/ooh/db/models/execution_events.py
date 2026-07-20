from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column
from ooh.db.models.enums import ExecutionEventType


class ExecutionEventCursor(Base):
    __tablename__ = "execution_event_cursors"
    __table_args__ = (
        sa.CheckConstraint("singleton_id = 1", name="ck_execution_event_cursors_singleton"),
    )

    singleton_id: Mapped[int] = mapped_column(
        sa.SmallInteger,
        primary_key=True,
        autoincrement=False,
    )
    last_event_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    __table_args__ = (
        sa.Index("ix_execution_events_repository_id", "repository_id", "id"),
        sa.Index("ix_execution_events_job_id", "job_id", "id"),
        sa.Index("ix_execution_events_agent_run_id", "agent_run_id", "id"),
        sa.Index("ix_execution_events_agent_step_id", "agent_step_id", "id"),
    )

    # ExecutionEventRepo assigns this while holding the singleton cursor row lock. Unlike a
    # database sequence, that makes IDs follow commit visibility and prevents replay gaps.
    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=False)
    repository_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
    )
    job_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="SET NULL"),
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
    )
    agent_step_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("agent_steps.id", ondelete="SET NULL"),
    )
    event_type: Mapped[ExecutionEventType] = enum_column(ExecutionEventType, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = created_at_column()


class ExecutionEventRead(OrmModel):
    id: int
    repository_id: UUID | None
    job_id: UUID | None
    agent_run_id: UUID | None
    agent_step_id: UUID | None
    event_type: ExecutionEventType
    payload: dict[str, Any]
    created_at: datetime
