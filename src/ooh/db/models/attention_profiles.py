from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, uuid_pk


class AttentionProfile(Base):
    __tablename__ = "attention_profiles"
    __table_args__ = (
        sa.UniqueConstraint("repository_id", "name", name="uq_attention_profiles_repository_name"),
        sa.Index(
            "uq_attention_profiles_one_active",
            "repository_id",
            unique=True,
            postgresql_where=sa.text("active"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    repository_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    default_weight: Mapped[Decimal] = mapped_column(sa.Numeric(6, 2), nullable=False, server_default="1.0")
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    created_at: Mapped[datetime] = created_at_column()


class AttentionProfileRead(OrmModel):
    id: UUID
    repository_id: UUID
    name: str
    default_weight: Decimal
    active: bool
    created_at: datetime
