import logging
import signal
import time

from ooh.config import get_settings
from ooh.db import check_database
from ooh.logging import configure_logging

logger = logging.getLogger(__name__)


class WorkerProcess:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.should_stop = False

    def request_stop(self, signum: int, _frame: object) -> None:
        logger.info("worker stop requested", extra={"signal": signum})
        self.should_stop = True

    def run(self) -> None:
        configure_logging(self.settings.log_level)
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        check_database()
        logger.info("worker started")

        while not self.should_stop:
            logger.debug("worker idle tick")
            time.sleep(self.settings.worker_poll_interval_seconds)

        logger.info("worker stopped")


def main() -> None:
    WorkerProcess().run()


if __name__ == "__main__":
    main()
