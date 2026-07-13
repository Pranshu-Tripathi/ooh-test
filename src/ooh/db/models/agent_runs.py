from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, uuid_pk
from ooh.db.models.enums import AgentRunType, AgentStatus


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        sa.Index("ix_agent_runs_job", "job_id"),
        sa.Index("ix_agent_runs_repository_created", "repository_id", "created_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    job_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="SET NULL"))
    repository_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
    )
    run_type: Mapped[AgentRunType] = enum_column(AgentRunType, nullable=False)
    status: Mapped[AgentStatus] = enum_column(AgentStatus, nullable=False, server_default="queued")
    model_profile: Mapped[str | None] = mapped_column(sa.Text)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()


class AgentRunRead(OrmModel):
    id: UUID
    job_id: UUID | None
    repository_id: UUID | None
    run_type: AgentRunType
    status: AgentStatus
    model_profile: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
