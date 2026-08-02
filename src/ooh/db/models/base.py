from datetime import datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StringEnumType(sa.TypeDecorator):
    impl = sa.Text
    cache_ok = True

    def __init__(self, enum_type: type[StrEnum]) -> None:
        super().__init__()
        self.enum_type = enum_type

    def process_bind_param(self, value: StrEnum | str | None, _dialect: sa.Dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, self.enum_type):
            return value.value
        return self.enum_type(value).value

    def process_result_value(self, value: str | None, _dialect: sa.Dialect) -> StrEnum | None:
        if value is None:
            return None
        return self.enum_type(value)


def uuid_pk() -> Mapped[UUID]:
    return mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def created_at_column() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def updated_at_column() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def enum_column(enum_type: type[StrEnum], *args: object, **kwargs: object) -> Mapped[StrEnum]:
    return mapped_column(StringEnumType(enum_type), *args, **kwargs)
