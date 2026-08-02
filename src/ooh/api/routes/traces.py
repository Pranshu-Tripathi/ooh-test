import asyncio
from collections.abc import AsyncIterator, Callable
from time import monotonic
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from ooh.api.errors import raise_http_for_service_error
from ooh.api.schemas.traces import (
    AgentArtifactResponse,
    AgentRunResponse,
    AgentRunSnapshotResponse,
    AgentStepResponse,
    ExecutionEventResponse,
    ProvenanceRefResponse,
)
from ooh.db.models import AgentStatus, ExecutionEventRead
from ooh.services import ServiceError, build_trace_service

router = APIRouter(tags=["traces"])
trace_service = build_trace_service()
EVENT_STREAM_PAGE_SIZE = 500
EVENT_STREAM_POLL_INTERVAL_SECONDS = 0.5
EVENT_STREAM_HEARTBEAT_SECONDS = 15.0
EVENT_STREAM_RETRY_MILLISECONDS = 1_000
TERMINAL_AGENT_STATUSES = {
    AgentStatus.SUCCEEDED,
    AgentStatus.FAILED,
    AgentStatus.CANCELLED,
    AgentStatus.INTERRUPTED,
}
EventFetcher = Callable[[int], list[ExecutionEventRead]]
CompletionCheck = Callable[[], bool]


@router.get(
    "/repositories/{repository_id}/agent-runs",
    response_model=list[AgentRunResponse],
)
def list_repository_agent_runs(repository_id: UUID) -> list[AgentRunResponse]:
    try:
        runs = trace_service.list_repository_runs(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [AgentRunResponse.from_record(run) for run in runs]


@router.get("/agent-runs/{agent_run_id}", response_model=AgentRunResponse)
def get_agent_run(agent_run_id: UUID) -> AgentRunResponse:
    try:
        run = trace_service.get_run(agent_run_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return AgentRunResponse.from_record(run)


@router.get(
    "/agent-runs/{agent_run_id}/snapshot",
    response_model=AgentRunSnapshotResponse,
)
def get_agent_run_snapshot(agent_run_id: UUID) -> AgentRunSnapshotResponse:
    try:
        snapshot = trace_service.get_run_snapshot(agent_run_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return AgentRunSnapshotResponse.from_snapshot(snapshot)


@router.get(
    "/agent-runs/{agent_run_id}/events",
    response_model=list[ExecutionEventResponse],
)
def list_agent_run_events(
    agent_run_id: UUID,
    after_event_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 500,
) -> list[ExecutionEventResponse]:
    try:
        events = trace_service.list_run_events(
            agent_run_id,
            after_event_id=after_event_id,
            limit=limit,
        )
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [ExecutionEventResponse.from_record(event) for event in events]


@router.get("/agent-runs/{agent_run_id}/events/stream")
def stream_agent_run_events(
    agent_run_id: UUID,
    request: Request,
    after_event_id: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[
        int | None,
        Header(alias="Last-Event-ID", ge=0),
    ] = None,
) -> StreamingResponse:
    try:
        trace_service.get_run(agent_run_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)

    resume_after = resolve_resume_event_id(
        after_event_id=after_event_id,
        last_event_id=last_event_id,
    )
    return execution_event_stream_response(
        request=request,
        after_event_id=resume_after,
        fetch_events=lambda cursor: trace_service.poll_run_events(
            agent_run_id,
            after_event_id=cursor,
            limit=EVENT_STREAM_PAGE_SIZE,
        ),
        is_complete=lambda: trace_service.get_run(agent_run_id).status in TERMINAL_AGENT_STATUSES,
    )


@router.get(
    "/agent-runs/{agent_run_id}/steps",
    response_model=list[AgentStepResponse],
)
def list_agent_run_steps(agent_run_id: UUID) -> list[AgentStepResponse]:
    try:
        steps = trace_service.list_run_steps(agent_run_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [AgentStepResponse.from_record(step) for step in steps]


@router.get(
    "/repositories/{repository_id}/events",
    response_model=list[ExecutionEventResponse],
)
def list_repository_events(
    repository_id: UUID,
    after_event_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 500,
) -> list[ExecutionEventResponse]:
    try:
        events = trace_service.list_repository_events(
            repository_id,
            after_event_id=after_event_id,
            limit=limit,
        )
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [ExecutionEventResponse.from_record(event) for event in events]


@router.get("/repositories/{repository_id}/events/stream")
def stream_repository_events(
    repository_id: UUID,
    request: Request,
    after_event_id: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[
        int | None,
        Header(alias="Last-Event-ID", ge=0),
    ] = None,
) -> StreamingResponse:
    try:
        trace_service.ensure_repository_exists(repository_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)

    resume_after = resolve_resume_event_id(
        after_event_id=after_event_id,
        last_event_id=last_event_id,
    )
    return execution_event_stream_response(
        request=request,
        after_event_id=resume_after,
        fetch_events=lambda cursor: trace_service.poll_repository_events(
            repository_id,
            after_event_id=cursor,
            limit=EVENT_STREAM_PAGE_SIZE,
        ),
    )


@router.get(
    "/agent-steps/{agent_step_id}/artifacts",
    response_model=list[AgentArtifactResponse],
)
def list_agent_step_artifacts(agent_step_id: UUID) -> list[AgentArtifactResponse]:
    try:
        artifacts = trace_service.list_step_artifacts(agent_step_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [AgentArtifactResponse.from_record(artifact) for artifact in artifacts]


@router.get(
    "/agent-artifacts/{agent_artifact_id}/provenance-refs",
    response_model=list[ProvenanceRefResponse],
)
def list_agent_artifact_provenance_refs(agent_artifact_id: UUID) -> list[ProvenanceRefResponse]:
    try:
        provenance_refs = trace_service.list_artifact_provenance_refs(agent_artifact_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return [ProvenanceRefResponse.from_record(provenance_ref) for provenance_ref in provenance_refs]


def execution_event_stream_response(
    *,
    request: Request,
    after_event_id: int,
    fetch_events: EventFetcher,
    is_complete: CompletionCheck | None = None,
) -> StreamingResponse:
    return StreamingResponse(
        iter_execution_event_sse(
            request=request,
            after_event_id=after_event_id,
            fetch_events=fetch_events,
            is_complete=is_complete,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


async def iter_execution_event_sse(
    *,
    request: Request,
    after_event_id: int,
    fetch_events: EventFetcher,
    is_complete: CompletionCheck | None = None,
    poll_interval_seconds: float = EVENT_STREAM_POLL_INTERVAL_SECONDS,
    heartbeat_seconds: float = EVENT_STREAM_HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    cursor = after_event_id
    last_emit_at = monotonic()
    yield f"retry: {EVENT_STREAM_RETRY_MILLISECONDS}\n\n"

    while not await request.is_disconnected():
        events = await asyncio.to_thread(fetch_events, cursor)
        if events:
            for event in events:
                cursor = event.id
                last_emit_at = monotonic()
                yield encode_execution_event_sse(event)
            continue

        if is_complete is not None and await asyncio.to_thread(is_complete):
            # The terminal state and its event commit together, but that commit can land between
            # the empty poll above and the status read. Poll once more before closing so the
            # terminal event cannot be skipped at that boundary.
            terminal_events = await asyncio.to_thread(fetch_events, cursor)
            if terminal_events:
                for event in terminal_events:
                    cursor = event.id
                    last_emit_at = monotonic()
                    yield encode_execution_event_sse(event)
                continue
            return

        if monotonic() - last_emit_at >= heartbeat_seconds:
            last_emit_at = monotonic()
            yield ": keep-alive\n\n"
        await asyncio.sleep(poll_interval_seconds)


def encode_execution_event_sse(event: ExecutionEventRead) -> str:
    response = ExecutionEventResponse.from_record(event)
    return (
        f"id: {event.id}\nevent: {event.event_type.value}\ndata: {response.model_dump_json()}\n\n"
    )


def resolve_resume_event_id(*, after_event_id: int, last_event_id: int | None) -> int:
    return max(after_event_id, last_event_id or 0)
