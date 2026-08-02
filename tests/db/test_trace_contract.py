from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest

from ooh.api.schemas.traces import AgentStepResponse, ExecutionEventResponse
from ooh.db.models import (
    AgentActivity,
    AgentRunType,
    AgentStatus,
    AgentStep,
    AgentStepRead,
    AgentStepType,
    ExecutionEvent,
    ExecutionEventCursor,
    ExecutionEventRead,
    ExecutionEventType,
)
from ooh.db.repos import ExecutionEventInput, ExecutionEventRepo


def test_agent_step_metadata_exposes_causal_contract() -> None:
    columns = AgentStep.__table__.columns

    assert {"parent_step_id", "context_pack_id", "activity", "iteration"} <= set(columns.keys())
    assert columns.parent_step_id.nullable
    assert columns.context_pack_id.nullable
    assert columns.activity.nullable
    assert columns.iteration.nullable
    assert {constraint.name for constraint in AgentStep.__table__.constraints} >= {
        "ck_agent_steps_iteration_positive",
        "ck_agent_steps_not_own_parent",
    }


def test_trace_contract_can_represent_ingestion_and_interrupted_work() -> None:
    assert AgentRunType.REPOSITORY_INGESTION == "repository_ingestion_run"
    assert AgentStatus.INTERRUPTED == "interrupted"


def test_execution_event_metadata_uses_monotonic_resume_cursor() -> None:
    table = ExecutionEvent.__table__
    cursor_table = ExecutionEventCursor.__table__

    assert table.c.id.primary_key
    assert table.c.id.autoincrement is False
    assert table.c.event_type.nullable is False
    assert table.c.payload.nullable is False
    assert cursor_table.c.singleton_id.primary_key
    assert cursor_table.c.singleton_id.autoincrement is False
    assert cursor_table.c.last_event_id.nullable is False
    assert {constraint.name for constraint in cursor_table.constraints} >= {
        "ck_execution_event_cursors_singleton"
    }
    assert {index.name for index in table.indexes} >= {
        "ix_execution_events_repository_id",
        "ix_execution_events_job_id",
        "ix_execution_events_agent_run_id",
        "ix_execution_events_agent_step_id",
    }


def test_agent_step_response_includes_causal_and_activity_fields() -> None:
    now = datetime.now(UTC)
    run_id = uuid4()
    parent_step_id = uuid4()
    context_pack_id = uuid4()
    step = AgentStepRead(
        id=uuid4(),
        agent_run_id=run_id,
        parent_step_id=parent_step_id,
        context_pack_id=context_pack_id,
        step_type=AgentStepType.MODEL_CALL,
        status=AgentStatus.RUNNING,
        activity=AgentActivity.WAITING_ON_MODEL,
        sequence=10,
        iteration=2,
        input_summary={"model": "qwen3:8b"},
        output_summary={},
        warning_summary=[],
        started_at=now,
        finished_at=None,
        created_at=now,
    )

    response = AgentStepResponse.from_record(step)

    assert response.parent_step_id == parent_step_id
    assert response.context_pack_id == context_pack_id
    assert response.activity == AgentActivity.WAITING_ON_MODEL
    assert response.iteration == 2


def test_execution_event_response_preserves_resume_id_and_scope() -> None:
    now = datetime.now(UTC)
    repository_id = uuid4()
    run_id = uuid4()
    event = ExecutionEventRead(
        id=42,
        repository_id=repository_id,
        job_id=None,
        agent_run_id=run_id,
        agent_step_id=None,
        event_type=ExecutionEventType.RUN_STATUS_CHANGED,
        payload={"status": "running"},
        created_at=now,
    )

    response = ExecutionEventResponse.from_record(event)

    assert response.id == 42
    assert response.repository_id == repository_id
    assert response.agent_run_id == run_id
    assert response.payload == {"status": "running"}


def test_execution_event_repo_rejects_unscoped_events_before_database_access() -> None:
    repo = ExecutionEventRepo(cast(Any, None))

    with pytest.raises(ValueError, match="scope identifier"):
        repo.create(ExecutionEventInput(event_type=ExecutionEventType.RUN_CREATED))


@pytest.mark.parametrize(
    ("after_event_id", "limit"),
    [(-1, 100), (0, 0), (0, 1_001)],
)
def test_execution_event_cursor_bounds(after_event_id: int, limit: int) -> None:
    with pytest.raises(ValueError):
        ExecutionEventRepo._validate_cursor(after_event_id=after_event_id, limit=limit)
