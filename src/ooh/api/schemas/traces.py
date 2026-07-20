from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel

from ooh.db.models import (
    AgentActivity,
    AgentArtifactRead,
    AgentArtifactType,
    AgentRunRead,
    AgentRunType,
    AgentStatus,
    AgentStepRead,
    AgentStepType,
    ExecutionEventRead,
    ExecutionEventType,
    ProvenanceRefRead,
    ProvenanceRefType,
)

if TYPE_CHECKING:
    from ooh.services.traces import AgentRunSnapshot


class AgentRunResponse(BaseModel):
    id: UUID
    job_id: UUID | None
    repository_id: UUID | None
    run_type: AgentRunType
    status: AgentStatus
    model_profile: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    @classmethod
    def from_record(cls, run: AgentRunRead) -> "AgentRunResponse":
        return cls(
            id=run.id,
            job_id=run.job_id,
            repository_id=run.repository_id,
            run_type=run.run_type,
            status=run.status,
            model_profile=run.model_profile,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
        )


class AgentStepResponse(BaseModel):
    id: UUID
    agent_run_id: UUID
    parent_step_id: UUID | None
    context_pack_id: UUID | None
    step_type: AgentStepType
    status: AgentStatus
    activity: AgentActivity | None
    sequence: int
    iteration: int | None
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    warning_summary: list[dict[str, Any]]
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    @classmethod
    def from_record(cls, step: AgentStepRead) -> "AgentStepResponse":
        return cls(
            id=step.id,
            agent_run_id=step.agent_run_id,
            parent_step_id=step.parent_step_id,
            context_pack_id=step.context_pack_id,
            step_type=step.step_type,
            status=step.status,
            activity=step.activity,
            sequence=step.sequence,
            iteration=step.iteration,
            input_summary=step.input_summary,
            output_summary=step.output_summary,
            warning_summary=step.warning_summary,
            started_at=step.started_at,
            finished_at=step.finished_at,
            created_at=step.created_at,
        )


class ExecutionEventResponse(BaseModel):
    id: int
    repository_id: UUID | None
    job_id: UUID | None
    agent_run_id: UUID | None
    agent_step_id: UUID | None
    event_type: ExecutionEventType
    payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_record(cls, event: ExecutionEventRead) -> "ExecutionEventResponse":
        return cls(
            id=event.id,
            repository_id=event.repository_id,
            job_id=event.job_id,
            agent_run_id=event.agent_run_id,
            agent_step_id=event.agent_step_id,
            event_type=event.event_type,
            payload=event.payload,
            created_at=event.created_at,
        )


class AgentArtifactResponse(BaseModel):
    id: UUID
    agent_step_id: UUID
    artifact_type: AgentArtifactType
    artifact_uri: str
    content_hash: str | None
    created_at: datetime

    @classmethod
    def from_record(cls, artifact: AgentArtifactRead) -> "AgentArtifactResponse":
        return cls(
            id=artifact.id,
            agent_step_id=artifact.agent_step_id,
            artifact_type=artifact.artifact_type,
            artifact_uri=artifact.artifact_uri,
            content_hash=artifact.content_hash,
            created_at=artifact.created_at,
        )


class AgentRunSnapshotResponse(BaseModel):
    run: AgentRunResponse
    steps: list[AgentStepResponse]
    artifacts: list[AgentArtifactResponse]
    last_event_id: int

    @classmethod
    def from_snapshot(cls, snapshot: AgentRunSnapshot) -> "AgentRunSnapshotResponse":
        return cls(
            run=AgentRunResponse.from_record(snapshot.run),
            steps=[AgentStepResponse.from_record(step) for step in snapshot.steps],
            artifacts=[
                AgentArtifactResponse.from_record(artifact) for artifact in snapshot.artifacts
            ],
            last_event_id=snapshot.last_event_id,
        )


class ProvenanceRefResponse(BaseModel):
    id: UUID
    artifact_id: UUID
    ref_type: ProvenanceRefType
    ref_uri: str
    content_hash: str | None
    metadata: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_record(cls, provenance_ref: ProvenanceRefRead) -> "ProvenanceRefResponse":
        return cls(
            id=provenance_ref.id,
            artifact_id=provenance_ref.artifact_id,
            ref_type=provenance_ref.ref_type,
            ref_uri=provenance_ref.ref_uri,
            content_hash=provenance_ref.content_hash,
            metadata=provenance_ref.metadata,
            created_at=provenance_ref.created_at,
        )
