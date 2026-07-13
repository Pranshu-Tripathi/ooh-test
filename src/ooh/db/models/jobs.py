from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, updated_at_column, uuid_pk
from ooh.db.models.enums import JobStatus, JobType


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        sa.CheckConstraint(
            "status in ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
        sa.Index("ix_jobs_claimable", "status", "run_after", "created_at"),
        sa.Index("ix_jobs_repository", "repository_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    repository_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
    )
    job_type: Mapped[JobType] = enum_column(JobType, nullable=False)
    status: Mapped[JobStatus] = enum_column(JobStatus, nullable=False, server_default="queued")
    attempt_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="3")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    run_after: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    locked_by: Mapped[str | None] = mapped_column(sa.Text)
    locked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class JobRead(OrmModel):
    id: UUID
    repository_id: UUID | None
    job_type: JobType
    status: JobStatus
    attempt_count: int
    max_attempts: int
    payload: dict[str, Any]
    run_after: datetime
    locked_by: str | None
    locked_at: datetime | None
    error_summary: str | None
    created_at: datetime
    updated_at: datetime
