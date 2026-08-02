from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, uuid_pk


class AttentionFocusArea(Base):
    __tablename__ = "attention_focus_areas"
    __table_args__ = (
        sa.UniqueConstraint("attention_profile_id", "name", name="uq_attention_focus_areas_profile_name"),
    )

    id: Mapped[UUID] = uuid_pk()
    attention_profile_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("attention_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    weight: Mapped[Decimal] = mapped_column(sa.Numeric(6, 2), nullable=False, server_default="1.0")
    path_globs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    created_at: Mapped[datetime] = created_at_column()


class AttentionFocusAreaRead(OrmModel):
    id: UUID
    attention_profile_id: UUID
    name: str
    description: str | None
    weight: Decimal
    path_globs: list[str]
    created_at: datetime
