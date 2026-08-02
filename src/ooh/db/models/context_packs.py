from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, uuid_pk
from ooh.db.models.enums import ContextPackType


class ContextPack(Base):
    __tablename__ = "context_packs"
    __table_args__ = (sa.Index("ix_context_packs_snapshot", "snapshot_id", "pack_type"),)

    id: Mapped[UUID] = uuid_pk()
    repository_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repo_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    attention_profile_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("attention_profiles.id", ondelete="SET NULL"),
    )
    pack_type: Mapped[ContextPackType] = enum_column(ContextPackType, nullable=False)
    artifact_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = created_at_column()


class ContextPackRead(OrmModel):
    id: UUID
    repository_id: UUID
    snapshot_id: UUID
    attention_profile_id: UUID | None
    pack_type: ContextPackType
    artifact_uri: str
    content_hash: str | None
    created_at: datetime
