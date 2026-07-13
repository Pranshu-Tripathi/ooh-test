from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, updated_at_column, uuid_pk
from ooh.db.models.enums import GuidanceSourceType


class GuidanceSource(Base):
    __tablename__ = "guidance_sources"
    __table_args__ = (
        sa.UniqueConstraint("repository_id", "path", name="uq_guidance_sources_repository_path"),
        sa.Index("ix_guidance_sources_enabled", "repository_id", "enabled"),
    )

    id: Mapped[UUID] = uuid_pk()
    repository_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[GuidanceSourceType] = enum_column(GuidanceSourceType, nullable=False)
    path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(sa.Text)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("true"))
    last_indexed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class GuidanceSourceRead(OrmModel):
    id: UUID
    repository_id: UUID
    source_type: GuidanceSourceType
    path: str
    content_hash: str | None
    enabled: bool
    last_indexed_at: datetime | None
    created_at: datetime
    updated_at: datetime
