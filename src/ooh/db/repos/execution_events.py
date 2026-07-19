from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ooh.db.connection import Database
from ooh.db.models import (
    ExecutionEvent,
    ExecutionEventCursor,
    ExecutionEventRead,
    ExecutionEventType,
)


@dataclass(frozen=True)
class ExecutionEventInput:
    event_type: ExecutionEventType
    repository_id: UUID | None = None
    job_id: UUID | None = None
    agent_run_id: UUID | None = None
    agent_step_id: UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class ExecutionEventRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, input: ExecutionEventInput) -> ExecutionEventRead:
        _validate_event_input(input)
        with self.db.session() as session:
            return append_execution_event(session, input)

    def list_for_agent_run(
        self,
        agent_run_id: UUID,
        *,
        after_event_id: int = 0,
        limit: int = 500,
    ) -> list[ExecutionEventRead]:
        self._validate_cursor(after_event_id=after_event_id, limit=limit)
        with self.db.session() as session:
            events = session.scalars(
                select(ExecutionEvent)
                .where(
                    ExecutionEvent.agent_run_id == agent_run_id,
                    ExecutionEvent.id > after_event_id,
                )
                .order_by(ExecutionEvent.id)
                .limit(limit)
            ).all()
            return [ExecutionEventRead.model_validate(event) for event in events]

    def list_for_repository(
        self,
        repository_id: UUID,
        *,
        after_event_id: int = 0,
        limit: int = 500,
    ) -> list[ExecutionEventRead]:
        self._validate_cursor(after_event_id=after_event_id, limit=limit)
        with self.db.session() as session:
            events = session.scalars(
                select(ExecutionEvent)
                .where(
                    ExecutionEvent.repository_id == repository_id,
                    ExecutionEvent.id > after_event_id,
                )
                .order_by(ExecutionEvent.id)
                .limit(limit)
            ).all()
            return [ExecutionEventRead.model_validate(event) for event in events]

    def latest_id_for_agent_run(self, agent_run_id: UUID) -> int:
        with self.db.session() as session:
            latest_event_id = session.scalar(
                select(func.max(ExecutionEvent.id)).where(
                    ExecutionEvent.agent_run_id == agent_run_id
                )
            )
            return int(latest_event_id or 0)

    def latest_id_for_repository(self, repository_id: UUID) -> int:
        with self.db.session() as session:
            latest_event_id = session.scalar(
                select(func.max(ExecutionEvent.id)).where(
                    ExecutionEvent.repository_id == repository_id
                )
            )
            return int(latest_event_id or 0)

    @staticmethod
    def _validate_cursor(*, after_event_id: int, limit: int) -> None:
        if after_event_id < 0:
            raise ValueError("after_event_id cannot be negative")
        if limit < 1 or limit > 1_000:
            raise ValueError("execution event limit must be between 1 and 1000")


def append_execution_event(
    session: Session,
    input: ExecutionEventInput,
) -> ExecutionEventRead:
    """Append an event inside an existing state-change transaction."""
    _validate_event_input(input)

    cursor = session.scalar(
        select(ExecutionEventCursor).where(ExecutionEventCursor.singleton_id == 1).with_for_update()
    )
    if cursor is None:
        raise RuntimeError("execution event cursor is not initialized")
    cursor.last_event_id += 1
    event = ExecutionEvent(
        id=cursor.last_event_id,
        repository_id=input.repository_id,
        job_id=input.job_id,
        agent_run_id=input.agent_run_id,
        agent_step_id=input.agent_step_id,
        event_type=input.event_type,
        payload=input.payload,
    )
    session.add(event)
    session.flush()
    session.refresh(event)
    return ExecutionEventRead.model_validate(event)


def _validate_event_input(input: ExecutionEventInput) -> None:
    if not any((input.repository_id, input.job_id, input.agent_run_id, input.agent_step_id)):
        raise ValueError("execution event requires at least one scope identifier")
