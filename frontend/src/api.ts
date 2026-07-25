import type {
  AgentArtifact,
  AgentRun,
  AgentRunSnapshot,
  AgentStep,
  AttentionProfile,
  ContextPack,
  DriftEvent,
  ExecutionEvent,
  GeneratedTest,
  Job,
  JobDetail,
  ProvenanceRef,
  Repository,
  RepositorySchedule,
  SavedLearning,
  TestAnswer,
  TestResult
} from "./types";

type RequestOptions = {
  method?: "GET" | "POST" | "PUT";
  body?: unknown;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers:
      options.body === undefined
        ? undefined
        : {
            "content-type": "application/json"
          },
    body: options.body === undefined ? undefined : JSON.stringify(options.body)
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) {
        message = payload.detail;
      }
    } catch {
      // Keep the HTTP status fallback.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export const api = {
  listRepositories: () => request<Repository[]>("/repositories"),
  getRepository: (repositoryId: string) => request<Repository>(`/repositories/${repositoryId}`),
  registerRepository: (payload: {
    name?: string;
    source_type: "local_path" | "github";
    source_uri: string;
    default_branch?: string;
  }) =>
    request<{ repository: Repository; ingest_job: Job }>("/repositories", {
      method: "POST",
      body: payload
    }),
  enqueueIngest: (repositoryId: string) =>
    request<Job>(`/repositories/${repositoryId}/ingest-jobs`, { method: "POST" }),
  enqueueGenerateTest: (repositoryId: string, packTypes?: string[]) =>
    request<Job>(`/repositories/${repositoryId}/generate-test-jobs`, {
      method: "POST",
      body: packTypes?.length ? { pack_types: packTypes } : {}
    }),
  buildContextPacks: (repositoryId: string) =>
    request<ContextPack[]>(`/repositories/${repositoryId}/context-packs`, { method: "POST" }),
  listContextPacks: (repositoryId: string) =>
    request<ContextPack[]>(`/repositories/${repositoryId}/context-packs`),
  listGeneratedTests: (repositoryId: string) =>
    request<GeneratedTest[]>(`/repositories/${repositoryId}/generated-tests`),
  listDriftEvents: (repositoryId: string) =>
    request<DriftEvent[]>(`/repositories/${repositoryId}/drift-events`),
  listAttentionProfiles: (repositoryId: string) =>
    request<AttentionProfile[]>(`/repositories/${repositoryId}/attention-profiles`),
  getRepositorySchedule: (repositoryId: string) =>
    request<RepositorySchedule | null>(`/repositories/${repositoryId}/schedule`),
  updateRepositorySchedule: (
    repositoryId: string,
    payload: {
      enabled: boolean;
      drift_min_score: string;
      drift_max_score: string | null;
      pack_types: string[];
    }
  ) =>
    request<RepositorySchedule>(`/repositories/${repositoryId}/schedule`, {
      method: "PUT",
      body: payload
    }),
  listRepositoryResults: (repositoryId: string) =>
    request<TestResult[]>(`/repositories/${repositoryId}/test-results`),
  listLearnings: (repositoryId: string) =>
    request<SavedLearning[]>(`/repositories/${repositoryId}/learnings`),
  getJob: (jobId: string) => request<JobDetail>(`/jobs/${jobId}`),
  listAgentRuns: (repositoryId: string) =>
    request<AgentRun[]>(`/repositories/${repositoryId}/agent-runs`),
  getAgentRun: (agentRunId: string) => request<AgentRun>(`/agent-runs/${agentRunId}`),
  getAgentRunSnapshot: (agentRunId: string) =>
    request<AgentRunSnapshot>(`/agent-runs/${agentRunId}/snapshot`),
  listAgentRunEvents: (agentRunId: string, afterEventId = 0) =>
    request<ExecutionEvent[]>(
      `/agent-runs/${agentRunId}/events?after_event_id=${encodeURIComponent(afterEventId)}`
    ),
  agentRunEventStreamUrl: (agentRunId: string, afterEventId = 0) =>
    `/agent-runs/${agentRunId}/events/stream?after_event_id=${encodeURIComponent(afterEventId)}`,
  listRepositoryEvents: (repositoryId: string, afterEventId = 0) =>
    request<ExecutionEvent[]>(
      `/repositories/${repositoryId}/events?after_event_id=${encodeURIComponent(afterEventId)}`
    ),
  repositoryEventStreamUrl: (repositoryId: string, afterEventId = 0) =>
    `/repositories/${repositoryId}/events/stream?after_event_id=${encodeURIComponent(afterEventId)}`,
  listAgentSteps: (agentRunId: string) =>
    request<AgentStep[]>(`/agent-runs/${agentRunId}/steps`),
  listAgentArtifacts: (agentStepId: string) =>
    request<AgentArtifact[]>(`/agent-steps/${agentStepId}/artifacts`),
  listArtifactProvenance: (artifactId: string) =>
    request<ProvenanceRef[]>(`/agent-artifacts/${artifactId}/provenance-refs`),
  listAnswers: (generatedTestId: string) =>
    request<TestAnswer[]>(`/generated-tests/${generatedTestId}/answers`),
  listResults: (generatedTestId: string) =>
    request<TestResult[]>(`/generated-tests/${generatedTestId}/results`),
  submitAnswer: (generatedTestId: string, answerText: string) =>
    request<{ answer: TestAnswer; judge_job: Job }>(`/generated-tests/${generatedTestId}/answers`, {
      method: "POST",
      body: { answer_text: answerText }
    })
};
