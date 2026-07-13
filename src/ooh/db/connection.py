from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import sqlalchemy as sa
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ooh.config import get_settings
from ooh.db.alembic_config import build_alembic_config, sqlalchemy_database_url


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(sqlalchemy_database_url(settings.database_url), pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def get_session() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database() -> None:
    with get_engine().connect() as connection:
        connection.execute(sa.text("select 1")).scalar_one()


def check_schema_current() -> None:
    config = build_alembic_config()
    expected_heads = set(ScriptDirectory.from_config(config).get_heads())

    with get_engine().connect() as connection:
        version_table = connection.execute(sa.text("select to_regclass('public.alembic_version')")).scalar_one()
        if version_table is None:
            raise RuntimeError("database schema has not been migrated")

        actual_heads = set(connection.execute(sa.text("select version_num from alembic_version")).scalars())

    if actual_heads != expected_heads:
        raise RuntimeError(
            f"database schema is not current: expected {sorted(expected_heads)}, got {sorted(actual_heads)}"
        )
