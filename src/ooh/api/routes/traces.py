from uuid import UUID

from fastapi import APIRouter

from ooh.api.errors import raise_http_for_service_error
from ooh.api.schemas.traces import (
    AgentArtifactResponse,
    AgentRunResponse,
    AgentStepResponse,
    ProvenanceRefResponse,
)
from ooh.services import ServiceError, build_trace_service

router = APIRouter(tags=["traces"])
trace_service = build_trace_service()


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
    return [
        ProvenanceRefResponse.from_record(provenance_ref)
        for provenance_ref in provenance_refs
    ]
