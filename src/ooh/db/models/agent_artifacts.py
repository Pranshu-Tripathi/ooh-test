from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, uuid_pk
from ooh.db.models.enums import AgentArtifactType


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"
    __table_args__ = (sa.Index("ix_agent_artifacts_step", "agent_step_id"),)

    id: Mapped[UUID] = uuid_pk()
    agent_step_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("agent_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_type: Mapped[AgentArtifactType] = enum_column(AgentArtifactType, nullable=False)
    artifact_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = created_at_column()


class AgentArtifactRead(OrmModel):
    id: UUID
    agent_step_id: UUID
    artifact_type: AgentArtifactType
    artifact_uri: str
    content_hash: str | None
    created_at: datetime
