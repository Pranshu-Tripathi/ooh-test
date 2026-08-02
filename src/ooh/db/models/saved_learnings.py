from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, uuid_pk


class SavedLearning(Base):
    __tablename__ = "saved_learnings"
    __table_args__ = (sa.Index("ix_saved_learnings_repository_created", "repository_id", "created_at"),)

    id: Mapped[UUID] = uuid_pk()
    repository_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    test_result_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("test_results.id", ondelete="SET NULL"),
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    summary: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = created_at_column()


class SavedLearningRead(OrmModel):
    id: UUID
    repository_id: UUID
    test_result_id: UUID | None
    title: str
    summary: str
    source_payload: dict[str, Any]
    created_at: datetime
