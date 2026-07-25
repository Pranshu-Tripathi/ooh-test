import logging
import signal
import sys
from threading import Event

from ooh.config import get_settings
from ooh.db import check_database, check_schema_current
from ooh.logging import configure_logging
from ooh.services import build_scheduler_service

logger = logging.getLogger(__name__)


class SchedulerProcess:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.stop_event = Event()
        self.scheduler_service = build_scheduler_service()

    def request_stop(self, signum: int, _frame: object) -> None:
        logger.info("scheduler stop requested", extra={"signal": signum})
        self.stop_event.set()

    def run(self, *, once: bool = False) -> None:
        configure_logging(self.settings.log_level, log_format=self.settings.log_format)
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        check_database()
        check_schema_current()
        logger.info(
            "scheduler started poll_interval_seconds=%s batch_size=%s once=%s",
            self.settings.scheduler_poll_interval_seconds,
            self.settings.scheduler_batch_size,
            once,
        )

        while not self.stop_event.is_set():
            result = self.scheduler_service.tick(
                batch_size=self.settings.scheduler_batch_size
            )
            if result.evaluated_count:
                logger.info(
                    "scheduler tick evaluated=%s triggered=%s",
                    result.evaluated_count,
                    result.triggered_count,
                )
            if once:
                break
            self.stop_event.wait(self.settings.scheduler_poll_interval_seconds)

        logger.info("scheduler stopped")


def main() -> None:
    unknown_arguments = [argument for argument in sys.argv[1:] if argument != "--once"]
    if unknown_arguments:
        raise SystemExit(f"unknown scheduler arguments: {', '.join(unknown_arguments)}")
    SchedulerProcess().run(once="--once" in sys.argv[1:])


if __name__ == "__main__":
    main()
