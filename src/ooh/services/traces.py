from dataclasses import dataclass
from uuid import UUID

from ooh.db.models import (
    AgentArtifactRead,
    AgentRunRead,
    AgentStepRead,
    ExecutionEventRead,
    ProvenanceRefRead,
)
from ooh.db.repos import AgentTraceRepo, ExecutionEventRepo, RepositoryRepo
from ooh.services.exceptions import NotFoundError


@dataclass(frozen=True)
class AgentRunSnapshot:
    run: AgentRunRead
    steps: list[AgentStepRead]
    artifacts: list[AgentArtifactRead]
    last_event_id: int


class TraceService:
    def __init__(
        self,
        *,
        agent_trace_repo: AgentTraceRepo,
        execution_event_repo: ExecutionEventRepo,
        repository_repo: RepositoryRepo,
    ) -> None:
        self.agent_trace_repo = agent_trace_repo
        self.execution_event_repo = execution_event_repo
        self.repository_repo = repository_repo

    def list_repository_runs(self, repository_id: UUID) -> list[AgentRunRead]:
        if self.repository_repo.get(repository_id) is None:
            raise NotFoundError("repository not found")
        return self.agent_trace_repo.list_runs_for_repository(repository_id)

    def get_run(self, run_id: UUID) -> AgentRunRead:
        run = self.agent_trace_repo.get_run(run_id)
        if run is None:
            raise NotFoundError("agent run not found")
        return run

    def list_run_steps(self, run_id: UUID) -> list[AgentStepRead]:
        self.get_run(run_id)
        return self.agent_trace_repo.list_steps_for_run(run_id)

    def get_run_snapshot(self, run_id: UUID) -> AgentRunSnapshot:
        # Capture the replay cursor before reading state. Concurrent changes can then appear in
        # both the snapshot and replay, but can never be skipped by a reconnecting client.
        last_event_id = self.execution_event_repo.latest_id_for_agent_run(run_id)
        run = self.get_run(run_id)
        return AgentRunSnapshot(
            run=run,
            steps=self.agent_trace_repo.list_steps_for_run(run_id),
            artifacts=self.agent_trace_repo.list_artifacts_for_run(run_id),
            last_event_id=last_event_id,
        )

    def list_run_events(
        self,
        run_id: UUID,
        *,
        after_event_id: int = 0,
        limit: int = 500,
    ) -> list[ExecutionEventRead]:
        self.get_run(run_id)
        return self.poll_run_events(
            run_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    def poll_run_events(
        self,
        run_id: UUID,
        *,
        after_event_id: int,
        limit: int = 500,
    ) -> list[ExecutionEventRead]:
        return self.execution_event_repo.list_for_agent_run(
            run_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    def list_repository_events(
        self,
        repository_id: UUID,
        *,
        after_event_id: int = 0,
        limit: int = 500,
    ) -> list[ExecutionEventRead]:
        self.ensure_repository_exists(repository_id)
        return self.poll_repository_events(
            repository_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    def poll_repository_events(
        self,
        repository_id: UUID,
        *,
        after_event_id: int,
        limit: int = 500,
    ) -> list[ExecutionEventRead]:
        return self.execution_event_repo.list_for_repository(
            repository_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    def list_step_artifacts(self, step_id: UUID) -> list[AgentArtifactRead]:
        step = self.agent_trace_repo.get_step(step_id)
        if step is None:
            raise NotFoundError("agent step not found")
        return self.agent_trace_repo.list_artifacts_for_step(step_id)

    def list_artifact_provenance_refs(self, artifact_id: UUID) -> list[ProvenanceRefRead]:
        artifact = self.agent_trace_repo.get_artifact(artifact_id)
        if artifact is None:
            raise NotFoundError("agent artifact not found")
        return self.agent_trace_repo.list_provenance_refs_for_artifact(artifact_id)

    def ensure_repository_exists(self, repository_id: UUID) -> None:
        if self.repository_repo.get(repository_id) is None:
            raise NotFoundError("repository not found")
