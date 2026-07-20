from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, uuid_pk
from ooh.db.models.enums import AgentActivity, AgentStatus, AgentStepType


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        sa.UniqueConstraint("agent_run_id", "sequence", name="uq_agent_steps_run_sequence"),
        sa.Index("ix_agent_steps_run", "agent_run_id", "sequence"),
        sa.Index("ix_agent_steps_parent", "parent_step_id"),
        sa.Index("ix_agent_steps_context_pack", "context_pack_id"),
        sa.CheckConstraint(
            "iteration is null or iteration > 0",
            name="ck_agent_steps_iteration_positive",
        ),
        sa.CheckConstraint(
            "parent_step_id is null or parent_step_id <> id",
            name="ck_agent_steps_not_own_parent",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    agent_run_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_step_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("agent_steps.id", ondelete="SET NULL"),
    )
    context_pack_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("context_packs.id", ondelete="SET NULL"),
    )
    step_type: Mapped[AgentStepType] = enum_column(AgentStepType, nullable=False)
    status: Mapped[AgentStatus] = enum_column(AgentStatus, nullable=False, server_default="queued")
    activity: Mapped[AgentActivity | None] = enum_column(AgentActivity)
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    iteration: Mapped[int | None] = mapped_column(sa.Integer)
    input_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    output_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
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
    parent_step_id: UUID | None = None
    context_pack_id: UUID | None = None
    step_type: AgentStepType
    status: AgentStatus
    activity: AgentActivity | None = None
    sequence: int
    iteration: int | None = None
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    warning_summary: list[dict[str, Any]]
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
