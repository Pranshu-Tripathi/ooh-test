from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, updated_at_column, uuid_pk
from ooh.db.models.enums import RepositorySourceType, RepositoryStatus


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        sa.CheckConstraint("source_type in ('local_path', 'github')", name="ck_repositories_source_type"),
        sa.Index("ix_repositories_source_type", "source_type"),
        sa.Index("ix_repositories_status", "status"),
    )

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_type: Mapped[RepositorySourceType] = enum_column(RepositorySourceType, nullable=False)
    source_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    default_branch: Mapped[str | None] = mapped_column(sa.Text)
    token_ref: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[RepositoryStatus] = enum_column(RepositoryStatus, nullable=False, server_default="pending")
    last_processed_commit_sha: Mapped[str | None] = mapped_column(sa.Text)
    last_indexed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class RepositoryRead(OrmModel):
    id: UUID
    name: str
    source_type: RepositorySourceType
    source_uri: str
    default_branch: str | None
    token_ref: str | None
    status: RepositoryStatus
    last_processed_commit_sha: str | None
    last_indexed_at: datetime | None
    created_at: datetime
    updated_at: datetime
