from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

DEFAULT_AGENT_LOOP_TIMEOUT_SECONDS = 600.0


class AgentLoopDeadlineExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class LoopDeadline:
    expires_at: float
    clock: Callable[[], float] = monotonic

    @classmethod
    def start(
        cls,
        duration_seconds: float = DEFAULT_AGENT_LOOP_TIMEOUT_SECONDS,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> "LoopDeadline":
        if duration_seconds <= 0:
            raise ValueError("agent loop duration_seconds must be positive")
        return cls(expires_at=clock() + duration_seconds, clock=clock)

    def remaining_seconds(self, *, action: str) -> float:
        remaining = self.expires_at - self.clock()
        if remaining <= 0:
            raise AgentLoopDeadlineExceeded(
                f"agent loop exceeded its deadline before {action}"
            )
        return remaining
