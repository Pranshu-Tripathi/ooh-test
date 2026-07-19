from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import Mock
from uuid import uuid4

import pytest

from ooh.db.models import (
    AgentArtifactRead,
    AgentArtifactType,
    AgentRunRead,
    AgentRunType,
    AgentStatus,
    AgentStepRead,
    AgentStepType,
    ExecutionEventRead,
    ExecutionEventType,
)
from ooh.services import NotFoundError
from ooh.services.traces import TraceService


def test_run_snapshot_captures_replay_cursor_before_reading_state() -> None:
    calls: list[str] = []
    run = build_run()
    step = build_step(run.id)
    artifact = build_artifact(step.id)
    agent_trace_repo = Mock()
    execution_event_repo = Mock()
    repository_repo = Mock()

    execution_event_repo.latest_id_for_agent_run.side_effect = lambda _run_id: (
        calls.append("cursor") or 42
    )
    agent_trace_repo.get_run.side_effect = lambda _run_id: calls.append("run") or run
    agent_trace_repo.list_steps_for_run.return_value = [step]
    agent_trace_repo.list_artifacts_for_run.return_value = [artifact]
    service = TraceService(
        agent_trace_repo=cast(Any, agent_trace_repo),
        execution_event_repo=cast(Any, execution_event_repo),
        repository_repo=cast(Any, repository_repo),
    )

    snapshot = service.get_run_snapshot(run.id)

    assert calls[:2] == ["cursor", "run"]
    assert snapshot.run == run
    assert snapshot.steps == [step]
    assert snapshot.artifacts == [artifact]
    assert snapshot.last_event_id == 42


def test_run_event_replay_validates_run_and_passes_cursor() -> None:
    run = build_run()
    event = build_event(run.id, event_id=8)
    agent_trace_repo = Mock()
    execution_event_repo = Mock()
    agent_trace_repo.get_run.return_value = run
    execution_event_repo.list_for_agent_run.return_value = [event]
    service = TraceService(
        agent_trace_repo=cast(Any, agent_trace_repo),
        execution_event_repo=cast(Any, execution_event_repo),
        repository_repo=cast(Any, Mock()),
    )

    events = service.list_run_events(run.id, after_event_id=7, limit=25)

    assert events == [event]
    execution_event_repo.list_for_agent_run.assert_called_once_with(
        run.id,
        after_event_id=7,
        limit=25,
    )


def test_repository_event_replay_rejects_unknown_repository() -> None:
    repository_repo = Mock()
    repository_repo.get.return_value = None
    service = TraceService(
        agent_trace_repo=cast(Any, Mock()),
        execution_event_repo=cast(Any, Mock()),
        repository_repo=cast(Any, repository_repo),
    )

    with pytest.raises(NotFoundError, match="repository not found"):
        service.list_repository_events(uuid4())


def build_run() -> AgentRunRead:
    current_time = datetime.now(UTC)
    return AgentRunRead(
        id=uuid4(),
        job_id=uuid4(),
        repository_id=uuid4(),
        run_type=AgentRunType.TEST_GENERATION,
        status=AgentStatus.RUNNING,
        model_profile="qwen3-coder:8b",
        started_at=current_time,
        finished_at=None,
        created_at=current_time,
    )


def build_step(agent_run_id: Any) -> AgentStepRead:
    current_time = datetime.now(UTC)
    return AgentStepRead(
        id=uuid4(),
        agent_run_id=agent_run_id,
        parent_step_id=None,
        context_pack_id=None,
        step_type=AgentStepType.MODEL_CALL,
        status=AgentStatus.RUNNING,
        activity=None,
        sequence=1,
        iteration=1,
        input_summary={},
        output_summary={},
        warning_summary=[],
        started_at=current_time,
        finished_at=None,
        created_at=current_time,
    )


def build_artifact(agent_step_id: Any) -> AgentArtifactRead:
    return AgentArtifactRead(
        id=uuid4(),
        agent_step_id=agent_step_id,
        artifact_type=AgentArtifactType.PROMPT,
        artifact_uri="/tmp/prompt.json",
        content_hash="prompt-hash",
        created_at=datetime.now(UTC),
    )


def build_event(agent_run_id: Any, *, event_id: int) -> ExecutionEventRead:
    return ExecutionEventRead(
        id=event_id,
        repository_id=uuid4(),
        job_id=uuid4(),
        agent_run_id=agent_run_id,
        agent_step_id=None,
        event_type=ExecutionEventType.RUN_STATUS_CHANGED,
        payload={"run": {"status": "running"}},
        created_at=datetime.now(UTC),
    )
