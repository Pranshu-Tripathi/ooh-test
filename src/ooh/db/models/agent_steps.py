from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, uuid_pk
from ooh.db.models.enums import AgentStatus, AgentStepType


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        sa.UniqueConstraint("agent_run_id", "sequence", name="uq_agent_steps_run_sequence"),
        sa.Index("ix_agent_steps_run", "agent_run_id", "sequence"),
    )

    id: Mapped[UUID] = uuid_pk()
    agent_run_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_type: Mapped[AgentStepType] = enum_column(AgentStepType, nullable=False)
    status: Mapped[AgentStatus] = enum_column(AgentStatus, nullable=False, server_default="queued")
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    warning_summary: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()


class AgentStepRead(OrmModel):
    id: UUID
    agent_run_id: UUID
    step_type: AgentStepType
    status: AgentStatus
    sequence: int
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    warning_summary: list[dict[str, Any]]
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
