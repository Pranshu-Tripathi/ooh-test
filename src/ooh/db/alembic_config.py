from pathlib import Path

from alembic.config import Config

from ooh.config import get_settings


def sqlalchemy_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


def build_alembic_config() -> Config:
    project_root = next(
        path for path in Path(__file__).resolve().parents if (path / "alembic.ini").exists()
    )
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", sqlalchemy_database_url(get_settings().database_url))
    return config
