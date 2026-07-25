import logging
import os
import signal
import socket
from threading import Event

from ooh.config import get_settings
from ooh.db import check_database, check_schema_current, get_database
from ooh.logging import configure_logging
from ooh.worker.runner import JobRunner

logger = logging.getLogger(__name__)


class WorkerProcess:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.stop_event = Event()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.runner = JobRunner(get_database(), worker_id=self.worker_id)

    def request_stop(self, signum: int, _frame: object) -> None:
        logger.info("worker stop requested", extra={"signal": signum})
        self.stop_event.set()

    def run(self) -> None:
        configure_logging(self.settings.log_level, log_format=self.settings.log_format)
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        check_database()
        check_schema_current()
        logger.info(
            "worker started",
            extra={
                "worker_id": self.worker_id,
                "job_types": (
                    sorted(job_type.value for job_type in self.runner.job_types)
                    if self.runner.job_types is not None
                    else ["all"]
                ),
                "lease_seconds": self.settings.worker_lease_seconds,
            },
        )

        while not self.stop_event.is_set():
            processed = self.runner.process_once()
            if not processed:
                logger.debug("worker idle tick", extra={"worker_id": self.worker_id})
                self.stop_event.wait(self.settings.worker_poll_interval_seconds)

        logger.info("worker stopped")


def main() -> None:
    WorkerProcess().run()


if __name__ == "__main__":
    main()
