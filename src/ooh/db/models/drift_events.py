from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, uuid_pk
from ooh.db.models.enums import DriftSeverity


class DriftEvent(Base):
    __tablename__ = "drift_events"
    __table_args__ = (sa.Index("ix_drift_events_repository_created", "repository_id", "created_at"),)

    id: Mapped[UUID] = uuid_pk()
    repository_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repo_snapshots.id", ondelete="SET NULL"),
    )
    from_commit_sha: Mapped[str | None] = mapped_column(sa.Text)
    to_commit_sha: Mapped[str] = mapped_column(sa.Text, nullable=False)
    drift_score: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), nullable=False, server_default="0")
    severity: Mapped[DriftSeverity] = enum_column(DriftSeverity, nullable=False, server_default="low")
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = created_at_column()


class DriftEventRead(OrmModel):
    id: UUID
    repository_id: UUID
    snapshot_id: UUID | None
    from_commit_sha: str | None
    to_commit_sha: str
    drift_score: Decimal
    severity: DriftSeverity
    breakdown: dict[str, Any]
    created_at: datetime
