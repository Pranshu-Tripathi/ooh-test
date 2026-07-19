from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ooh.db.connection import Database
from ooh.db.models import (
    AgentActivity,
    AgentArtifact,
    AgentArtifactRead,
    AgentArtifactType,
    AgentRun,
    AgentRunRead,
    AgentRunType,
    AgentStatus,
    AgentStep,
    AgentStepRead,
    AgentStepType,
    ContextPack,
    ExecutionEventType,
    ProvenanceRef,
    ProvenanceRefRead,
    ProvenanceRefType,
)
from ooh.db.repos.execution_events import ExecutionEventInput, append_execution_event


@dataclass(frozen=True)
class AgentRunInput:
    run_type: AgentRunType
    job_id: UUID | None = None
    repository_id: UUID | None = None
    status: AgentStatus = AgentStatus.QUEUED
    model_profile: str | None = None


@dataclass(frozen=True)
class AgentStepInput:
    agent_run_id: UUID
    step_type: AgentStepType
    sequence: int
    parent_step_id: UUID | None = None
    context_pack_id: UUID | None = None
    iteration: int | None = None
    status: AgentStatus = AgentStatus.QUEUED
    activity: AgentActivity | None = None
    input_summary: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)
    warning_summary: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentArtifactInput:
    agent_step_id: UUID
    artifact_type: AgentArtifactType
    artifact_uri: str
    content_hash: str | None = None


@dataclass(frozen=True)
class ProvenanceRefInput:
    artifact_id: UUID
    ref_type: ProvenanceRefType
    ref_uri: str
    content_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentTraceRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create_run(self, input: AgentRunInput) -> AgentRunRead:
        with self.db.session() as session:
            run = AgentRun(
                job_id=input.job_id,
                repository_id=input.repository_id,
                run_type=input.run_type,
                status=input.status,
                model_profile=input.model_profile,
            )
            session.add(run)
            session.flush()
            session.refresh(run)
            run_read = AgentRunRead.model_validate(run)
            self._append_run_event(session, run_read, ExecutionEventType.RUN_CREATED)
            return run_read

    def get_run(self, run_id: UUID) -> AgentRunRead | None:
        with self.db.session() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return None
            return AgentRunRead.model_validate(run)

    def list_runs_for_repository(
        self,
        repository_id: UUID,
        *,
        limit: int = 50,
    ) -> list[AgentRunRead]:
        with self.db.session() as session:
            runs = session.scalars(
                select(AgentRun)
                .where(AgentRun.repository_id == repository_id)
                .order_by(AgentRun.created_at.desc())
                .limit(limit)
            ).all()
            return [AgentRunRead.model_validate(run) for run in runs]

    def mark_run_running(self, run_id: UUID) -> AgentRunRead:
        with self.db.session() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise ValueError("agent run not found")

            run.status = AgentStatus.RUNNING
            run.started_at = run.started_at or datetime.now(UTC)
            session.flush()
            session.refresh(run)
            run_read = AgentRunRead.model_validate(run)
            self._append_run_event(session, run_read, ExecutionEventType.RUN_STATUS_CHANGED)
            return run_read

    def mark_run_finished(self, run_id: UUID, *, status: AgentStatus) -> AgentRunRead:
        with self.db.session() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise ValueError("agent run not found")

            run.status = status
            run.finished_at = datetime.now(UTC)
            session.flush()
            session.refresh(run)
            run_read = AgentRunRead.model_validate(run)
            self._append_run_event(session, run_read, ExecutionEventType.RUN_STATUS_CHANGED)
            return run_read

    def create_step(self, input: AgentStepInput) -> AgentStepRead:
        with self.db.session() as session:
            if input.iteration is not None and input.iteration < 1:
                raise ValueError("agent step iteration must be positive")

            agent_run = session.get(AgentRun, input.agent_run_id)
            if agent_run is None:
                raise ValueError("agent run not found")

            parent_step = None
            context_pack_id = input.context_pack_id
            if input.parent_step_id is not None:
                parent_step = session.get(AgentStep, input.parent_step_id)
                if parent_step is None:
                    raise ValueError("parent agent step not found")
                if parent_step.agent_run_id != input.agent_run_id:
                    raise ValueError("parent agent step must belong to the same agent run")
                if context_pack_id is None:
                    context_pack_id = parent_step.context_pack_id

            if context_pack_id is not None:
                context_pack = session.get(ContextPack, context_pack_id)
                if context_pack is None:
                    raise ValueError("context pack not found")
                if parent_step is not None and (
                    parent_step.context_pack_id is not None
                    and parent_step.context_pack_id != context_pack_id
                ):
                    raise ValueError("child and parent agent steps must use the same context pack")
                if (
                    agent_run.repository_id is not None
                    and context_pack.repository_id != agent_run.repository_id
                ):
                    raise ValueError(
                        "context pack and agent run must belong to the same repository"
                    )

            step = AgentStep(
                agent_run_id=input.agent_run_id,
                parent_step_id=input.parent_step_id,
                context_pack_id=context_pack_id,
                step_type=input.step_type,
                status=input.status,
                activity=input.activity,
                sequence=input.sequence,
                iteration=input.iteration,
                input_summary=input.input_summary,
                output_summary=input.output_summary,
                warning_summary=input.warning_summary,
            )
            session.add(step)
            session.flush()
            session.refresh(step)
            step_read = AgentStepRead.model_validate(step)
            self._append_step_event(
                session,
                agent_run=AgentRunRead.model_validate(agent_run),
                step=step_read,
                event_type=ExecutionEventType.STEP_CREATED,
            )
            return step_read

    def list_steps_for_run(self, agent_run_id: UUID) -> list[AgentStepRead]:
        with self.db.session() as session:
            steps = session.scalars(
                select(AgentStep)
                .where(AgentStep.agent_run_id == agent_run_id)
                .order_by(AgentStep.sequence)
            ).all()
            return [AgentStepRead.model_validate(step) for step in steps]

    def get_step(self, step_id: UUID) -> AgentStepRead | None:
        with self.db.session() as session:
            step = session.get(AgentStep, step_id)
            if step is None:
                return None
            return AgentStepRead.model_validate(step)

    def mark_step_running(
        self,
        step_id: UUID,
        *,
        activity: AgentActivity | None = None,
    ) -> AgentStepRead:
        with self.db.session() as session:
            step = session.get(AgentStep, step_id)
            if step is None:
                raise ValueError("agent step not found")

            step.status = AgentStatus.RUNNING
            step.activity = activity
            step.started_at = step.started_at or datetime.now(UTC)
            session.flush()
            session.refresh(step)
            step_read = AgentStepRead.model_validate(step)
            self._append_step_event_for_step(
                session,
                step=step,
                step_read=step_read,
                event_type=ExecutionEventType.STEP_STATUS_CHANGED,
            )
            return step_read

    def mark_step_activity(
        self,
        step_id: UUID,
        *,
        activity: AgentActivity,
    ) -> AgentStepRead:
        with self.db.session() as session:
            step = session.get(AgentStep, step_id)
            if step is None:
                raise ValueError("agent step not found")
            if step.finished_at is not None:
                raise ValueError("cannot change activity for a finished agent step")

            step.status = AgentStatus.RUNNING
            step.activity = activity
            step.started_at = step.started_at or datetime.now(UTC)
            session.flush()
            session.refresh(step)
            step_read = AgentStepRead.model_validate(step)
            self._append_step_event_for_step(
                session,
                step=step,
                step_read=step_read,
                event_type=ExecutionEventType.STEP_ACTIVITY_CHANGED,
            )
            return step_read

    def mark_step_finished(
        self,
        step_id: UUID,
        *,
        status: AgentStatus,
        output_summary: dict[str, Any] | None = None,
        warning_summary: list[dict[str, Any]] | None = None,
    ) -> AgentStepRead:
        with self.db.session() as session:
            step = session.get(AgentStep, step_id)
            if step is None:
                raise ValueError("agent step not found")

            step.status = status
            step.activity = None
            if output_summary is not None:
                step.output_summary = output_summary
            if warning_summary is not None:
                step.warning_summary = warning_summary
            step.finished_at = datetime.now(UTC)
            session.flush()
            session.refresh(step)
            step_read = AgentStepRead.model_validate(step)
            self._append_step_event_for_step(
                session,
                step=step,
                step_read=step_read,
                event_type=ExecutionEventType.STEP_STATUS_CHANGED,
            )
            return step_read

    def create_artifact(self, input: AgentArtifactInput) -> AgentArtifactRead:
        with self.db.session() as session:
            step = session.get(AgentStep, input.agent_step_id)
            if step is None:
                raise ValueError("agent step not found")
            artifact = AgentArtifact(
                agent_step_id=input.agent_step_id,
                artifact_type=input.artifact_type,
                artifact_uri=input.artifact_uri,
                content_hash=input.content_hash,
            )
            session.add(artifact)
            session.flush()
            session.refresh(artifact)
            artifact_read = AgentArtifactRead.model_validate(artifact)
            agent_run = self._get_run_for_step(session, step)
            append_execution_event(
                session,
                ExecutionEventInput(
                    event_type=ExecutionEventType.ARTIFACT_CREATED,
                    repository_id=agent_run.repository_id,
                    job_id=agent_run.job_id,
                    agent_run_id=agent_run.id,
                    agent_step_id=step.id,
                    payload={"artifact": artifact_read.model_dump(mode="json")},
                ),
            )
            return artifact_read

    def list_artifacts_for_step(self, agent_step_id: UUID) -> list[AgentArtifactRead]:
        with self.db.session() as session:
            artifacts = session.scalars(
                select(AgentArtifact)
                .where(AgentArtifact.agent_step_id == agent_step_id)
                .order_by(AgentArtifact.created_at)
            ).all()
            return [AgentArtifactRead.model_validate(artifact) for artifact in artifacts]

    def get_artifact(self, artifact_id: UUID) -> AgentArtifactRead | None:
        with self.db.session() as session:
            artifact = session.get(AgentArtifact, artifact_id)
            if artifact is None:
                return None
            return AgentArtifactRead.model_validate(artifact)

    def create_provenance_ref(self, input: ProvenanceRefInput) -> ProvenanceRefRead:
        with self.db.session() as session:
            provenance_ref = ProvenanceRef(
                artifact_id=input.artifact_id,
                ref_type=input.ref_type,
                ref_uri=input.ref_uri,
                content_hash=input.content_hash,
                metadata_=input.metadata,
            )
            session.add(provenance_ref)
            session.flush()
            session.refresh(provenance_ref)
            return ProvenanceRefRead.model_validate(provenance_ref)

    def list_provenance_refs_for_artifact(self, artifact_id: UUID) -> list[ProvenanceRefRead]:
        with self.db.session() as session:
            provenance_refs = session.scalars(
                select(ProvenanceRef)
                .where(ProvenanceRef.artifact_id == artifact_id)
                .order_by(ProvenanceRef.created_at)
            ).all()
            return [
                ProvenanceRefRead.model_validate(provenance_ref)
                for provenance_ref in provenance_refs
            ]

    @staticmethod
    def _append_run_event(
        session: Session,
        run: AgentRunRead,
        event_type: ExecutionEventType,
    ) -> None:
        append_execution_event(
            session,
            ExecutionEventInput(
                event_type=event_type,
                repository_id=run.repository_id,
                job_id=run.job_id,
                agent_run_id=run.id,
                payload={"run": run.model_dump(mode="json")},
            ),
        )

    @classmethod
    def _append_step_event_for_step(
        cls,
        session: Session,
        *,
        step: AgentStep,
        step_read: AgentStepRead,
        event_type: ExecutionEventType,
    ) -> None:
        cls._append_step_event(
            session,
            agent_run=cls._get_run_for_step(session, step),
            step=step_read,
            event_type=event_type,
        )

    @staticmethod
    def _append_step_event(
        session: Session,
        *,
        agent_run: AgentRunRead,
        step: AgentStepRead,
        event_type: ExecutionEventType,
    ) -> None:
        append_execution_event(
            session,
            ExecutionEventInput(
                event_type=event_type,
                repository_id=agent_run.repository_id,
                job_id=agent_run.job_id,
                agent_run_id=agent_run.id,
                agent_step_id=step.id,
                payload={"step": step.model_dump(mode="json")},
            ),
        )

    @staticmethod
    def _get_run_for_step(session: Session, step: AgentStep) -> AgentRunRead:
        agent_run = session.get(AgentRun, step.agent_run_id)
        if agent_run is None:
            raise ValueError("agent run not found")
        return AgentRunRead.model_validate(agent_run)
