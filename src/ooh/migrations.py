import logging

from alembic import command

from ooh.config import get_settings
from ooh.db.alembic_config import build_alembic_config
from ooh.logging import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, log_format=settings.log_format)
    command.upgrade(build_alembic_config(), "head")
    logger.info("database migrations applied")


if __name__ == "__main__":
    main()
