import logging

from ooh.config import get_settings
from ooh.db import check_database
from ooh.logging import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    check_database()
    logger.info("no migrations registered yet")


if __name__ == "__main__":
    main()
