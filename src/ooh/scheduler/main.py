import logging
import signal
import sys
from time import monotonic
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
        self.scheduler_service = build_scheduler_service(settings=self.settings)

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
            "scheduler started poll_interval_seconds=%s batch_size=%s "
            "repository_poll_interval_seconds=%s questions_per_category=%s once=%s",
            self.settings.scheduler_poll_interval_seconds,
            self.settings.scheduler_batch_size,
            self.settings.repository_poll_interval_seconds,
            self.settings.generation_questions_per_category,
            once,
        )

        next_repository_poll_at = 0.0
        while not self.stop_event.is_set():
            poll_repositories = monotonic() >= next_repository_poll_at
            poll_started_at = monotonic()
            result = self.scheduler_service.tick(
                batch_size=self.settings.scheduler_batch_size,
                poll_repositories=poll_repositories,
            )
            if result.repository_poll is not None:
                repository_poll = result.repository_poll
                logger.info(
                    "repository poll completed checked=%s unchanged=%s changed=%s "
                    "enqueued=%s active=%s failed=%s duration_ms=%s",
                    repository_poll.checked_count,
                    repository_poll.unchanged_count,
                    repository_poll.changed_count,
                    repository_poll.enqueued_count,
                    repository_poll.active_count,
                    repository_poll.failed_count,
                    round((monotonic() - poll_started_at) * 1000),
                )
                next_repository_poll_at = (
                    monotonic() + self.settings.repository_poll_interval_seconds
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
