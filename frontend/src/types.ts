export type RepositoryStatus = "pending" | "indexing" | "indexed" | "failed" | "needs_attention";
export type JobStatus = "queued" | "running" | "retry_wait" | "succeeded" | "failed" | "cancelled";
export type AgentStatus =
  | "queued"
  | "running"
  | "waiting_on_model"
  | "validating"
  | "succeeded"
  | "retrying"
  | "failed";

export type Repository = {
  id: string;
  name: string;
  source_type: "local_path" | "github";
  source_uri: string;
  default_branch: string | null;
  status: RepositoryStatus;
  last_processed_commit_sha: string | null;
  last_indexed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Job = {
  id: string;
  repository_id: string | null;
  job_type: string;
  status: JobStatus;
  result_metadata: Record<string, unknown>;
  created_at: string;
};

export type JobDetail = Job & {
  attempt_count: number;
  max_attempts: number;
  payload: Record<string, unknown>;
  run_after: string;
  locked_by: string | null;
  locked_at: string | null;
  error_summary: string | null;
  updated_at: string;
};

export type DriftEvent = {
  id: string;
  repository_id: string;
  snapshot_id: string | null;
  from_commit_sha: string | null;
  to_commit_sha: string;
  drift_score: string;
  severity: "low" | "medium" | "high";
  breakdown: Record<string, unknown>;
  created_at: string;
};

export type ContextPackSource = {
  id: string;
  context_pack_id: string;
  source_type: string;
  source_uri: string;
  content_hash: string | null;
  created_at: string;
};

export type ContextPack = {
  id: string;
  repository_id: string;
  snapshot_id: string;
  attention_profile_id: string | null;
  pack_type: string;
  artifact_uri: string;
  content_hash: string | null;
  sources: ContextPackSource[];
  created_at: string;
};

export type GeneratedTest = {
  id: string;
  repository_id: string;
  snapshot_id: string;
  drift_event_id: string | null;
  agent_run_id: string | null;
  context_pack_id: string | null;
  category: string;
  test_payload: Record<string, unknown>;
  evidence_refs: Record<string, unknown>[];
  prompt_version: string | null;
  created_at: string;
};

export type TestAnswer = {
  id: string;
  generated_test_id: string;
  answer_payload: Record<string, unknown>;
  submitted_at: string;
};

export type TestResult = {
  id: string;
  generated_test_id: string;
  test_answer_id: string | null;
  agent_run_id: string | null;
  score: string;
  status: string;
  feedback: Record<string, unknown>;
  alert_flag: boolean;
  created_at: string;
};

export type SavedLearning = {
  id: string;
  repository_id: string;
  test_result_id: string | null;
  title: string;
  summary: string;
  source_payload: Record<string, unknown>;
  created_at: string;
};

export type AttentionProfile = {
  id: string;
  repository_id: string;
  name: string;
  default_weight: string;
  active: boolean;
  focus_areas: Array<{
    id: string;
    attention_profile_id: string;
    name: string;
    description: string | null;
    weight: string;
    path_globs: string[];
    created_at: string;
  }>;
  created_at: string;
};

export type AgentRun = {
  id: string;
  job_id: string | null;
  repository_id: string | null;
  run_type: string;
  status: AgentStatus;
  model_profile: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type AgentStep = {
  id: string;
  agent_run_id: string;
  step_type: string;
  status: AgentStatus;
  sequence: number;
  input_summary: Record<string, unknown>;
  output_summary: Record<string, unknown>;
  warning_summary: Record<string, unknown>[];
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type AgentArtifact = {
  id: string;
  agent_step_id: string;
  artifact_type: string;
  artifact_uri: string;
  content_hash: string | null;
  created_at: string;
};

export type ProvenanceRef = {
  id: string;
  artifact_id: string;
  ref_type: string;
  ref_uri: string;
  content_hash: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};
