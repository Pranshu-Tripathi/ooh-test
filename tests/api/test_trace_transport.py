import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from ooh.api.routes.traces import (
    EVENT_STREAM_RETRY_MILLISECONDS,
    encode_execution_event_sse,
    iter_execution_event_sse,
    resolve_resume_event_id,
)
from ooh.db.models import ExecutionEventRead, ExecutionEventType


class ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


def test_sse_event_uses_durable_id_type_and_json_payload() -> None:
    event = build_event(42)

    encoded = encode_execution_event_sse(event)

    lines = encoded.strip().splitlines()
    assert lines[0] == "id: 42"
    assert lines[1] == "event: step_status_changed"
    payload = json.loads(lines[2].removeprefix("data: "))
    assert payload["id"] == 42
    assert payload["agent_run_id"] == str(event.agent_run_id)
    assert payload["payload"] == {"step": {"status": "running"}}


def test_sse_replays_events_then_closes_a_terminal_run() -> None:
    event = build_event(11)
    cursors: list[int] = []

    def fetch_events(cursor: int) -> list[ExecutionEventRead]:
        cursors.append(cursor)
        return [event] if len(cursors) == 1 else []

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in iter_execution_event_sse(
                request=ConnectedRequest(),
                after_event_id=10,
                fetch_events=fetch_events,
                is_complete=lambda: True,
                poll_interval_seconds=0,
            )
        ]

    chunks = asyncio.run(collect())

    assert chunks == [
        f"retry: {EVENT_STREAM_RETRY_MILLISECONDS}\n\n",
        encode_execution_event_sse(event),
    ]
    assert cursors == [10, 11, 11]


def test_sse_does_not_skip_event_committed_with_terminal_state() -> None:
    terminal_event = build_event(31)
    batches = iter([[], [terminal_event], [], []])

    def fetch_events(_cursor: int) -> list[ExecutionEventRead]:
        return next(batches)

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in iter_execution_event_sse(
                request=ConnectedRequest(),
                after_event_id=30,
                fetch_events=fetch_events,
                is_complete=lambda: True,
                poll_interval_seconds=0,
            )
        ]

    chunks = asyncio.run(collect())

    assert chunks[-1] == encode_execution_event_sse(terminal_event)


def test_resume_cursor_never_moves_backwards() -> None:
    assert resolve_resume_event_id(after_event_id=20, last_event_id=18) == 20
    assert resolve_resume_event_id(after_event_id=20, last_event_id=23) == 23
    assert resolve_resume_event_id(after_event_id=20, last_event_id=None) == 20


def build_event(event_id: int) -> ExecutionEventRead:
    return ExecutionEventRead(
        id=event_id,
        repository_id=uuid4(),
        job_id=uuid4(),
        agent_run_id=uuid4(),
        agent_step_id=uuid4(),
        event_type=ExecutionEventType.STEP_STATUS_CHANGED,
        payload={"step": {"status": "running"}},
        created_at=datetime.now(UTC),
    )
