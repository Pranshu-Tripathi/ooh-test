from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, uuid_pk
from ooh.db.models.enums import ContextPackSourceType


class ContextPackSource(Base):
    __tablename__ = "context_pack_sources"
    __table_args__ = (sa.Index("ix_context_pack_sources_pack", "context_pack_id"),)

    id: Mapped[UUID] = uuid_pk()
    context_pack_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("context_packs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[ContextPackSourceType] = enum_column(ContextPackSourceType, nullable=False)
    source_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = created_at_column()


class ContextPackSourceRead(OrmModel):
    id: UUID
    context_pack_id: UUID
    source_type: ContextPackSourceType
    source_uri: str
    content_hash: str | None
    created_at: datetime
