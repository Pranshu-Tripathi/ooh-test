from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, uuid_pk
from ooh.db.models.enums import RepoSnapshotStatus


class RepoSnapshot(Base):
    __tablename__ = "repo_snapshots"
    __table_args__ = (
        sa.Index("ix_repo_snapshots_repository_created", "repository_id", "created_at"),
        sa.Index("ix_repo_snapshots_commit_sha", "commit_sha"),
    )

    id: Mapped[UUID] = uuid_pk()
    repository_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    commit_sha: Mapped[str] = mapped_column(sa.Text, nullable=False)
    index_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[RepoSnapshotStatus] = enum_column(RepoSnapshotStatus, nullable=False, server_default="completed")
    created_at: Mapped[datetime] = created_at_column()


class RepoSnapshotRead(OrmModel):
    id: UUID
    repository_id: UUID
    commit_sha: str
    index_uri: str
    status: RepoSnapshotStatus
    created_at: datetime
