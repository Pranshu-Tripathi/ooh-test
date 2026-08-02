from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import ConfigDict, Field
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from ooh.db.models.base import Base, OrmModel, created_at_column, enum_column, uuid_pk
from ooh.db.models.enums import ProvenanceRefType


class ProvenanceRef(Base):
    __tablename__ = "provenance_refs"
    __table_args__ = (sa.Index("ix_provenance_refs_artifact", "artifact_id"),)

    id: Mapped[UUID] = uuid_pk()
    artifact_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        sa.ForeignKey("agent_artifacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    ref_type: Mapped[ProvenanceRefType] = enum_column(ProvenanceRefType, nullable=False)
    ref_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(sa.Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = created_at_column()


class ProvenanceRefRead(OrmModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    artifact_id: UUID
    ref_type: ProvenanceRefType
    ref_uri: str
    content_hash: str | None
    metadata: dict[str, Any] = Field(alias="metadata_")
    created_at: datetime
