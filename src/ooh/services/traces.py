from uuid import UUID

from ooh.db.models import AgentArtifactRead, AgentRunRead, AgentStepRead, ProvenanceRefRead
from ooh.db.repos import AgentTraceRepo, RepositoryRepo
from ooh.services.exceptions import NotFoundError


class TraceService:
    def __init__(
        self,
        *,
        agent_trace_repo: AgentTraceRepo,
        repository_repo: RepositoryRepo,
    ) -> None:
        self.agent_trace_repo = agent_trace_repo
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
