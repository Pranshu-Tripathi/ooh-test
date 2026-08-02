import logging
import os
import signal
import socket
import time

from ooh.config import get_settings
from ooh.db import check_database, check_schema_current, get_database
from ooh.logging import configure_logging
from ooh.worker.runner import JobRunner

logger = logging.getLogger(__name__)


class WorkerProcess:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.should_stop = False
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.runner = JobRunner(get_database(), worker_id=self.worker_id)

    def request_stop(self, signum: int, _frame: object) -> None:
        logger.info("worker stop requested", extra={"signal": signum})
        self.should_stop = True

    def run(self) -> None:
        configure_logging(self.settings.log_level)
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        check_database()
        check_schema_current()
        logger.info("worker started", extra={"worker_id": self.worker_id})

        while not self.should_stop:
            processed = self.runner.process_once()
            if not processed:
                logger.debug("worker idle tick", extra={"worker_id": self.worker_id})
                time.sleep(self.settings.worker_poll_interval_seconds)

        logger.info("worker stopped")


def main() -> None:
    WorkerProcess().run()


if __name__ == "__main__":
    main()
