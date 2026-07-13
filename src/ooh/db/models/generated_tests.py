from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, uuid_pk
from ooh.db.models.enums import GeneratedTestCategory


class GeneratedTest(Base):
    __tablename__ = "generated_tests"
    __table_args__ = (
        sa.Index("ix_generated_tests_repository_created", "repository_id", "created_at"),
        sa.Index("ix_generated_tests_snapshot", "snapshot_id"),
    )

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
    drift_event_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("drift_events.id", ondelete="SET NULL"),
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
    )
    context_pack_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("context_packs.id", ondelete="SET NULL"),
    )
    category: Mapped[GeneratedTestCategory] = enum_column(GeneratedTestCategory, nullable=False)
    test_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    prompt_version: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = created_at_column()


class GeneratedTestRead(OrmModel):
    id: UUID
    repository_id: UUID
    snapshot_id: UUID
    drift_event_id: UUID | None
    agent_run_id: UUID | None
    context_pack_id: UUID | None
    category: GeneratedTestCategory
    test_payload: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    prompt_version: str | None
    created_at: datetime
