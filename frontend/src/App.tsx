import {
  Activity,
  AlertCircle,
  Boxes,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  Clock3,
  Database,
  Eye,
  FileText,
  GitBranch,
  Home,
  Layers,
  Link2,
  Network,
  Package,
  Play,
  Plus,
  RefreshCw,
  Send,
  Settings,
  X,
  XCircle
} from "lucide-react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps
} from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode } from "elkjs/lib/elk-api";
import type { DependencyList, FormEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api";
import type {
  AgentArtifact,
  AgentRun,
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
  TestAnswerPayload,
  TestResult
} from "./types";

type Route =
  | { name: "repositories"; query: URLSearchParams }
  | { name: "repository"; repositoryId: string; query: URLSearchParams }
  | { name: "repositorySettings"; repositoryId: string; query: URLSearchParams }
  | { name: "job"; jobId: string; query: URLSearchParams }
  | { name: "generatedTest"; generatedTestId: string; query: URLSearchParams }
  | { name: "agentRun"; agentRunId: string; query: URLSearchParams };

type AsyncState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
};

type ArtifactsByStepId = Record<string, AgentArtifact[]>;
type TraceConnection = "idle" | "connecting" | "live" | "reconnecting" | "complete";

type LiveTraceState = {
  run: AgentRun | null;
  steps: AgentStep[];
  artifactsByStepId: ArtifactsByStepId;
  lastEventId: number;
  loading: boolean;
  error: string | null;
  connection: TraceConnection;
};

type AnswerDraft = {
  responseText: string;
  selectedOptionId: string;
  selectedOptionIds: string[];
};

type FocusAreaDraft = {
  key: string;
  name: string;
  description: string;
  pathGlobs: string;
  weight: string;
};

type TestSessionProgress = Record<
  string,
  {
    answers: TestAnswer[];
    results: TestResult[];
  }
>;

const EMPTY_ANSWER_DRAFT: AnswerDraft = {
  responseText: "",
  selectedOptionId: "",
  selectedOptionIds: []
};

const TRACE_EVENT_TYPES = [
  "run_created",
  "run_status_changed",
  "step_created",
  "step_status_changed",
  "step_activity_changed",
  "artifact_created"
] as const;

const TERMINAL_AGENT_STATUSES = new Set(["succeeded", "failed", "cancelled", "interrupted"]);

const PACK_TYPES = [
  "high_level_design",
  "low_level_components",
  "design_decisions",
  "future_improvements",
  "active_pr"
];

const DRIFT_PRESETS = [
  {
    name: "Low",
    score: "0",
    description: "Every recorded change"
  },
  {
    name: "Medium",
    score: "25",
    description: "Medium and high drift"
  },
  {
    name: "High",
    score: "80",
    description: "High-impact drift only"
  }
] as const;

type DriftPresetScore = (typeof DRIFT_PRESETS)[number]["score"];

function newFocusAreaDraft(): FocusAreaDraft {
  return {
    key: `${Date.now()}-${Math.random()}`,
    name: "",
    description: "",
    pathGlobs: "",
    weight: "1"
  };
}

export function App() {
  const route = useHashRoute();
  const repositories = useAsyncData(api.listRepositories, []);

  const reloadRepositories = useCallback(() => {
    void repositories.reload();
  }, [repositories]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#/">
          <span className="brand-mark">OOH</span>
          <span>
            <strong>Out Of Hands Test</strong>
            <small>Local inspection console</small>
          </span>
        </a>
        <div className="topbar-actions">
          <IconButton label="Refresh repositories" onClick={reloadRepositories}>
            <RefreshCw size={16} />
          </IconButton>
        </div>
      </header>

      <div className="workspace">
        <RepositorySidebar
          repositories={repositories.data ?? []}
          loading={repositories.loading}
          selectedRepositoryId={
            route.name === "repository" || route.name === "repositorySettings"
              ? route.repositoryId
              : null
          }
          onRegistered={reloadRepositories}
        />
        <main className="content">
          <RouteContent route={route} repositories={repositories.data ?? []} />
        </main>
      </div>
    </div>
  );
}

function RouteContent({ route, repositories }: { route: Route; repositories: Repository[] }) {
  if (route.name === "repository") {
    return <RepositoryWorkbench repositoryId={route.repositoryId} query={route.query} />;
  }
  if (route.name === "repositorySettings") {
    return <RepositorySettingsPage repositoryId={route.repositoryId} />;
  }
  if (route.name === "job") {
    return <JobPage jobId={route.jobId} />;
  }
  if (route.name === "generatedTest") {
    return (
      <GeneratedTestPage
        generatedTestId={route.generatedTestId}
        repositoryId={route.query.get("repositoryId")}
      />
    );
  }
  if (route.name === "agentRun") {
    return <AgentRunPage agentRunId={route.agentRunId} />;
  }
  return <RepositoryIndexPage repositories={repositories} />;
}

function RepositorySidebar({
  repositories,
  loading,
  selectedRepositoryId,
  onRegistered
}: {
  repositories: Repository[];
  loading: boolean;
  selectedRepositoryId: string | null;
  onRegistered: () => void;
}) {
  const [sourceUri, setSourceUri] = useState("/workspace");
  const [sourceType, setSourceType] = useState<"local_path" | "github">("local_path");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.registerRepository({
        name: name.trim() || undefined,
        source_type: sourceType,
        source_uri: sourceUri.trim()
      });
      onRegistered();
      navigate(`/repositories/${result.repository.id}`);
    } catch (exc) {
      setError(errorMessage(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-section">
        <div className="section-heading">
          <span>Repositories</span>
          {loading ? <CircleDashed size={15} className="spin" /> : null}
        </div>
        <div className="repo-list">
          {repositories.map((repository) => (
            <a
              className={`repo-item ${repository.id === selectedRepositoryId ? "active" : ""}`}
              href={`#/repositories/${repository.id}`}
              key={repository.id}
            >
              <span className="repo-name">{repository.name}</span>
              <span className="muted truncate">{repository.source_uri}</span>
              <StatusPill status={repository.status} />
            </a>
          ))}
          {!loading && repositories.length === 0 ? (
            <div className="empty-state">No repositories registered</div>
          ) : null}
        </div>
      </div>

      <form className="sidebar-section register-form" onSubmit={onSubmit}>
        <div className="section-heading">Register</div>
        <label>
          <span>Name</span>
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label>
          <span>Source</span>
          <select
            value={sourceType}
            onChange={(event) => setSourceType(event.target.value as "local_path" | "github")}
          >
            <option value="local_path">Local path</option>
            <option value="github">GitHub</option>
          </select>
        </label>
        <label>
          <span>URI</span>
          <input
            required
            value={sourceUri}
            onChange={(event) => setSourceUri(event.target.value)}
          />
        </label>
        {error ? <InlineError message={error} /> : null}
        <button className="primary action-button" disabled={busy} type="submit">
          <Plus size={15} />
          Register
        </button>
      </form>
    </aside>
  );
}

function RepositoryIndexPage({ repositories }: { repositories: Repository[] }) {
  const indexed = repositories.filter((repository) => repository.status === "indexed").length;
  const active = repositories.filter((repository) =>
    ["pending", "indexing", "needs_attention"].includes(repository.status)
  ).length;

  return (
    <div className="page stack">
      <PageTitle
        icon={<Home size={22} />}
        title="Repository index"
        eyebrow={`${repositories.length} registered`}
      />
      <div className="metric-grid">
        <Metric label="Indexed" value={indexed} tone="ok" />
        <Metric label="Active" value={active} tone="warn" />
        <Metric label="Failed" value={repositories.filter((repo) => repo.status === "failed").length} tone="bad" />
      </div>
      <section className="panel">
        <PanelHeader title="Recent repositories" icon={<Database size={17} />} />
        <div className="table-list">
          {repositories.map((repository) => (
            <a className="table-row" href={`#/repositories/${repository.id}`} key={repository.id}>
              <div>
                <strong>{repository.name}</strong>
                <span className="muted truncate">{repository.source_uri}</span>
              </div>
              <code>{shortSha(repository.last_processed_commit_sha)}</code>
              <StatusPill status={repository.status} />
              <ChevronRight size={16} />
            </a>
          ))}
          {repositories.length === 0 ? <div className="empty-state">No repository records</div> : null}
        </div>
      </section>
    </div>
  );
}

function RepositoryWorkbench({
  repositoryId,
  query
}: {
  repositoryId: string;
  query: URLSearchParams;
}) {
  const tab = query.get("tab") ?? "overview";
  const repository = useAsyncData(() => api.getRepository(repositoryId), [repositoryId]);
  const drift = useAsyncData(() => api.listDriftEvents(repositoryId), [repositoryId]);
  const packs = useAsyncData(() => api.listContextPacks(repositoryId), [repositoryId]);
  const tests = useAsyncData(() => api.listGeneratedTests(repositoryId), [repositoryId]);
  const runs = useAsyncData(() => api.listAgentRuns(repositoryId), [repositoryId]);
  const results = useAsyncData(() => api.listRepositoryResults(repositoryId), [repositoryId]);
  const learnings = useAsyncData(() => api.listLearnings(repositoryId), [repositoryId]);
  const runtime = useAsyncData(api.getRuntime, []);
  const [actionJob, setActionJob] = useState<Job | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selectedPackTypes, setSelectedPackTypes] = useState<string[]>(["low_level_components"]);
  const [questionCounts, setQuestionCounts] = useState<Record<string, number>>({});
  const defaultQuestionCount = runtime.data?.generation_questions_per_category ?? 1;
  const maxQuestionsPerCategory =
    runtime.data?.generation_max_questions_per_category ?? defaultQuestionCount;
  const maxQuestionsPerJob =
    runtime.data?.generation_max_questions_per_job ?? maxQuestionsPerCategory;
  const requestedQuestionCount = selectedPackTypes.reduce(
    (total, packType) => total + (questionCounts[packType] ?? defaultQuestionCount),
    0
  );

  const reloadAll = useCallback(() => {
    void repository.reload();
    void drift.reload();
    void packs.reload();
    void tests.reload();
    void runs.reload();
    void results.reload();
    void learnings.reload();
  }, [drift, learnings, packs, repository, results, runs, tests]);

  async function runAction(action: () => Promise<Job | ContextPack[]>) {
    setActionError(null);
    try {
      const result = await action();
      if (!Array.isArray(result)) {
        setActionJob(result);
      }
      reloadAll();
    } catch (exc) {
      setActionError(errorMessage(exc));
    }
  }

  if (repository.error) {
    return <FailurePanel title="Repository unavailable" message={repository.error} />;
  }

  return (
    <div className="page stack">
      <PageTitle
        icon={<Database size={22} />}
        title={repository.data?.name ?? "Repository"}
        eyebrow={repository.data?.source_type ?? "loading"}
        actions={
          <>
            <IconButton label="Refresh repository" onClick={reloadAll}>
              <RefreshCw size={16} />
            </IconButton>
            <a className="icon-link" href={`#/repositories/${repositoryId}/settings`}>
              <Settings size={16} />
              Settings
            </a>
          </>
        }
      />

      <div className="metric-grid">
        <Metric label="Status" value={repository.data?.status ?? "loading"} tone={statusTone(repository.data?.status)} />
        <Metric label="Commit" value={shortSha(repository.data?.last_processed_commit_sha)} />
        <Metric label="Agent runs" value={runs.data?.length ?? 0} />
        <Metric label="Tests" value={tests.data?.length ?? 0} />
      </div>

      <div className="toolbar">
        <button onClick={() => void runAction(() => api.enqueueIngest(repositoryId))}>
          <GitBranch size={15} />
          Ingest
        </button>
        <button onClick={() => void runAction(() => api.buildContextPacks(repositoryId))}>
          <Boxes size={15} />
          Build Packs
        </button>
        <button
          className="primary"
          disabled={
            runtime.loading ||
            runtime.error !== null ||
            selectedPackTypes.length === 0 ||
            requestedQuestionCount > maxQuestionsPerJob
          }
          onClick={() =>
            void runAction(() =>
              api.enqueueGenerateTest(
                repositoryId,
                selectedPackTypes.map((category) => ({
                  category,
                  question_count: questionCounts[category] ?? defaultQuestionCount
                }))
              )
            )
          }
        >
          <Play size={15} />
          Generate
        </button>
        <div className="segmented">
          {PACK_TYPES.map((packType) => (
            <button
              className={selectedPackTypes.includes(packType) ? "active" : ""}
              key={packType}
              onClick={() =>
                setSelectedPackTypes((current) =>
                  current.includes(packType)
                    ? current.filter((value) => value !== packType)
                    : [...current, packType]
                )
              }
            >
              {labelize(packType)}
            </button>
          ))}
        </div>
        <div className="generation-counts">
          {selectedPackTypes.map((packType) => (
            <label key={packType}>
              <span>{labelize(packType)} questions</span>
              <input
                max={maxQuestionsPerCategory}
                min="1"
                onChange={(event) =>
                  setQuestionCounts((current) => ({
                    ...current,
                    [packType]: Math.min(
                      maxQuestionsPerCategory,
                      Math.max(1, Number(event.target.value) || defaultQuestionCount)
                    )
                  }))
                }
                type="number"
                value={questionCounts[packType] ?? defaultQuestionCount}
              />
            </label>
          ))}
          <span
            className={
              requestedQuestionCount > maxQuestionsPerJob ? "generation-count-error" : "muted"
            }
          >
            {requestedQuestionCount}/{maxQuestionsPerJob} questions
          </span>
        </div>
      </div>
      {runtime.error ? (
        <InlineError message={`Generation configuration unavailable: ${runtime.error}`} />
      ) : null}
      {actionError ? <InlineError message={actionError} /> : null}
      {actionJob ? (
        <div className="notice">
          <Clock3 size={15} />
          <a href={`#/jobs/${actionJob.id}`}>{labelize(actionJob.job_type)}</a>
          <StatusPill status={actionJob.status} />
        </div>
      ) : null}

      <nav className="tabs">
        {["overview", "drift", "context", "tests", "runs"].map((item) => (
          <a
            className={tab === item ? "active" : ""}
            href={`#/repositories/${repositoryId}?tab=${item}`}
            key={item}
          >
            {labelize(item)}
          </a>
        ))}
      </nav>

      {tab === "drift" ? (
        <DriftTab drift={drift} />
      ) : tab === "context" ? (
        <ContextPackTab packs={packs} />
      ) : tab === "tests" ? (
        <GeneratedTestsTab tests={tests} results={results.data ?? []} repositoryId={repositoryId} />
      ) : tab === "runs" ? (
        <AgentRunsTab runs={runs} />
      ) : (
        <OverviewTab
          repository={repository.data}
          drift={drift.data ?? []}
          tests={tests.data ?? []}
          runs={runs.data ?? []}
          learnings={learnings.data ?? []}
        />
      )}
    </div>
  );
}

function OverviewTab({
  repository,
  drift,
  tests,
  runs,
  learnings
}: {
  repository: Repository | null;
  drift: DriftEvent[];
  tests: GeneratedTest[];
  runs: AgentRun[];
  learnings: SavedLearning[];
}) {
  return (
    <div className="two-column">
      <section className="panel">
        <PanelHeader title="Repository" icon={<Database size={17} />} />
        <dl className="definition-grid">
          <dt>Source URI</dt>
          <dd>{repository?.source_uri ?? "-"}</dd>
          <dt>Default branch</dt>
          <dd>{repository?.default_branch ?? "-"}</dd>
          <dt>Last indexed</dt>
          <dd>{formatDate(repository?.last_indexed_at)}</dd>
          <dt>Updated</dt>
          <dd>{formatDate(repository?.updated_at)}</dd>
        </dl>
      </section>
      <section className="panel">
        <PanelHeader title="Latest signals" icon={<Activity size={17} />} />
        <div className="signal-list">
          <Signal label="Latest drift" value={drift[0]?.severity ?? "none"} />
          <Signal label="Latest test" value={formatDate(tests[0]?.created_at)} />
          <Signal label="Latest run" value={runs[0]?.run_type ? labelize(runs[0].run_type) : "none"} />
          <Signal label="Learnings" value={learnings.length} />
        </div>
      </section>
    </div>
  );
}

function DriftTab({ drift }: { drift: AsyncState<DriftEvent[]> & { reload: () => Promise<void> } }) {
  return (
    <section className="panel">
      <PanelHeader title="Drift events" icon={<Activity size={17} />} />
      <AsyncBoundary state={drift}>
        <div className="table-list">
          {(drift.data ?? []).map((event) => (
            <div className="table-row tall" key={event.id}>
              <div>
                <strong>{formatDate(event.created_at)}</strong>
                <span className="muted">
                  {shortSha(event.from_commit_sha)} → {shortSha(event.to_commit_sha)}
                </span>
              </div>
              <StatusPill status={event.severity} />
              <code>{String(event.drift_score)}</code>
              <JsonPreview payload={event.breakdown} />
            </div>
          ))}
          {drift.data?.length === 0 ? <div className="empty-state">No drift events</div> : null}
        </div>
      </AsyncBoundary>
    </section>
  );
}

function ContextPackTab({ packs }: { packs: AsyncState<ContextPack[]> & { reload: () => Promise<void> } }) {
  return (
    <section className="panel">
      <PanelHeader title="Context packs" icon={<Boxes size={17} />} />
      <AsyncBoundary state={packs}>
        <div className="card-grid">
          {(packs.data ?? []).map((pack) => (
            <article className="item-card" key={pack.id}>
              <div className="item-card-header">
                <strong>{labelize(pack.pack_type)}</strong>
                <code>{shortId(pack.id)}</code>
              </div>
              <dl className="compact-definitions">
                <dt>Sources</dt>
                <dd>{pack.sources.length}</dd>
                <dt>Hash</dt>
                <dd>{shortSha(pack.content_hash)}</dd>
                <dt>Created</dt>
                <dd>{formatDate(pack.created_at)}</dd>
              </dl>
              <div className="source-list">
                {pack.sources.slice(0, 5).map((source) => (
                  <span className="source-pill" key={source.id}>
                    {source.source_type}: {source.source_uri}
                  </span>
                ))}
              </div>
            </article>
          ))}
          {packs.data?.length === 0 ? <div className="empty-state">No context packs</div> : null}
        </div>
      </AsyncBoundary>
    </section>
  );
}

function GeneratedTestsTab({
  tests,
  results,
  repositoryId
}: {
  tests: AsyncState<GeneratedTest[]> & { reload: () => Promise<void> };
  results: TestResult[];
  repositoryId: string;
}) {
  const resultsByTest = useMemo(() => {
    return results.reduce<Record<string, TestResult[]>>((acc, result) => {
      acc[result.generated_test_id] = [...(acc[result.generated_test_id] ?? []), result];
      return acc;
    }, {});
  }, [results]);
  const testSets = useMemo(() => groupGeneratedTests(tests.data ?? []), [tests.data]);

  return (
    <section className="panel">
      <PanelHeader title="Generated tests" icon={<FileText size={17} />} />
      <AsyncBoundary state={tests}>
        <div className="table-list">
          {testSets.map((testSet) => {
            const firstTest = testSet.tests[0];
            const resultCount = testSet.tests.reduce(
              (count, test) => count + (resultsByTest[test.id]?.length ?? 0),
              0
            );
            const categories = [...new Set(testSet.tests.map((test) => labelize(test.category)))];
            return (
              <a
                className="table-row tall"
                href={`#/generated-tests/${firstTest.id}?repositoryId=${repositoryId}`}
                key={testSet.id}
              >
                <div>
                  <strong>{testTitle(firstTest)}</strong>
                  <span className="muted">{categories.join(" · ")}</span>
                </div>
                <span>{testSet.tests.length} questions</span>
                <span>{resultCount} judged</span>
                <ChevronRight size={16} />
              </a>
            );
          })}
          {tests.data?.length === 0 ? <div className="empty-state">No generated tests</div> : null}
        </div>
      </AsyncBoundary>
    </section>
  );
}

function AgentRunsTab({ runs }: { runs: AsyncState<AgentRun[]> & { reload: () => Promise<void> } }) {
  const latestRunId = runs.data?.[0]?.id ?? null;
  const latestTrace = useTraceData(latestRunId);

  return (
    <div className="stack">
      <section className="panel">
        <PanelHeader title="Latest trace graph" icon={<Network size={17} />} />
        {latestRunId ? (
          <TracePreview
            artifactsByStepId={latestTrace.artifactsByStepId}
            connection={latestTrace.connection}
            error={latestTrace.error}
            loading={latestTrace.loading}
            onSelectStep={() => navigate(`/agent-runs/${latestRunId}`)}
            steps={latestTrace.steps}
          />
        ) : (
          <div className="empty-state">No agent runs</div>
        )}
      </section>
      <section className="panel">
        <PanelHeader title="Agent runs" icon={<Activity size={17} />} />
        <AsyncBoundary state={runs}>
          <div className="table-list">
            {(runs.data ?? []).map((run) => (
              <a className="table-row" href={`#/agent-runs/${run.id}`} key={run.id}>
                <div>
                  <strong>{labelize(run.run_type)}</strong>
                  <span className="muted">{run.model_profile ?? "model not recorded"}</span>
                </div>
                <StatusPill status={run.status} />
                <span>{formatDuration(run.started_at, run.finished_at)}</span>
                <ChevronRight size={16} />
              </a>
            ))}
            {runs.data?.length === 0 ? <div className="empty-state">No agent runs</div> : null}
          </div>
        </AsyncBoundary>
      </section>
    </div>
  );
}

function TracePreview({
  steps,
  artifactsByStepId,
  loading,
  error,
  connection,
  selectedStepId,
  onSelectStep
}: {
  steps: AgentStep[];
  artifactsByStepId: ArtifactsByStepId;
  loading: boolean;
  error: string | null;
  connection: TraceConnection;
  selectedStepId?: string | null;
  onSelectStep?: (stepId: string) => void;
}) {
  if (loading && steps.length === 0) {
    return (
      <div className="loading-row">
        <CircleDashed className="spin" size={16} />
        Loading trace graph
      </div>
    );
  }
  if (error && steps.length === 0) {
    return <InlineError message={error} />;
  }
  return (
    <TraceGraph
      artifactsByStepId={artifactsByStepId}
      connection={connection}
      onSelectStep={onSelectStep}
      selectedStepId={selectedStepId}
      steps={steps}
    />
  );
}

function TraceGraph({
  steps,
  artifactsByStepId,
  connection,
  selectedStepId,
  onSelectStep
}: {
  steps: AgentStep[];
  artifactsByStepId: ArtifactsByStepId;
  connection: TraceConnection;
  selectedStepId?: string | null;
  onSelectStep?: (stepId: string) => void;
}) {
  const [expandedSuccessfulGroups, setExpandedSuccessfulGroups] = useState<Set<string>>(
    () => new Set()
  );
  const artifactCount = Object.values(artifactsByStepId).reduce(
    (count, artifacts) => count + artifacts.length,
    0
  );
  const modelCallCount = steps.filter((step) => step.step_type === "model_call").length;
  const toolCallCount = steps.filter((step) => step.step_type === "tool_call").length;

  const toggleSuccessfulGroup = useCallback((contextPackId: string) => {
    setExpandedSuccessfulGroups((current) => {
      const next = new Set(current);
      if (next.has(contextPackId)) {
        next.delete(contextPackId);
      } else {
        next.add(contextPackId);
      }
      return next;
    });
  }, []);

  return (
    <div className="trace-graph-wrap">
      <div className="trace-legend">
        <TraceConnectionIndicator connection={connection} />
        <span>{steps.length} steps</span>
        <span>{modelCallCount} LLM calls</span>
        <span>{toolCallCount} tool calls</span>
        <span>{artifactCount} artifacts</span>
      </div>
      <ReactFlowProvider>
        <TraceFlowCanvas
          artifactsByStepId={artifactsByStepId}
          expandedSuccessfulGroups={expandedSuccessfulGroups}
          onSelectStep={onSelectStep}
          onToggleGroup={toggleSuccessfulGroup}
          selectedStepId={selectedStepId}
          steps={steps}
        />
      </ReactFlowProvider>
    </div>
  );
}

const TRACE_STEP_WIDTH = 320;
const TRACE_STEP_HEIGHT = 112;
const TRACE_GROUP_MIN_WIDTH = 420;
const TRACE_GROUP_HEADER_HEIGHT = 76;
const TRACE_GROUP_GAP = 92;
const traceElk = new ELK();

type TraceStepNodeData = {
  [key: string]: unknown;
  step: AgentStep;
  artifactCount: number;
  onSelectStep?: (stepId: string) => void;
};

type TraceGroupNodeData = {
  [key: string]: unknown;
  activity: string | null;
  branchLabel: string | null;
  collapsed: boolean;
  collapsible: boolean;
  contextPackId: string | null;
  eyebrow: string;
  nodeCount: number;
  onToggle?: (contextPackId: string) => void;
  status: string;
  title: string;
};

type TraceStepFlowNodeType = Node<TraceStepNodeData, "traceStep">;
type TraceGroupFlowNodeType = Node<TraceGroupNodeData, "traceGroup">;
type TraceFlowNode = TraceStepFlowNodeType | TraceGroupFlowNodeType;

type TraceStepLayout = {
  height: number;
  positions: Map<string, { x: number; y: number }>;
  width: number;
};

type TraceFlowGroupBlock = {
  collapsed: boolean;
  data: TraceGroupNodeData;
  groupId: string;
  height: number;
  layout: TraceStepLayout;
  steps: AgentStep[];
  width: number;
};

function TraceStepFlowNode({ data, selected }: NodeProps<TraceStepFlowNodeType>) {
  const { step, artifactCount, onSelectStep } = data;
  return (
    <div className="trace-flow-step">
      <Handle className="trace-flow-handle" position={Position.Top} type="target" />
      <button
        className={[
          "flow-execution-card",
          `status-${statusTone(step.status)}`,
          step.status === "running" ? "running" : "",
          selected ? "active" : ""
        ].join(" ")}
        onClick={() => onSelectStep?.(step.id)}
        type="button"
      >
        <span className="execution-node-icon">{stepNodeIcon(step)}</span>
        <span className="execution-node-copy">
          <span className="node-kind">
            {stepNodeKind(step)} · #{step.sequence}
            {step.iteration ? ` · iteration ${step.iteration}` : ""}
          </span>
          <strong>{stepNodeTitle(step)}</strong>
          <span className="step-detail">{stepNodeDetail(step) ?? stepStateSummary(step)}</span>
        </span>
        <span className="execution-node-meta">
          {step.activity ? <span className="activity-label">{labelize(step.activity)}</span> : null}
          <StatusPill status={step.status} />
          <span>{formatStepDuration(step)}</span>
          {artifactCount ? <span>{artifactCount} artifacts</span> : null}
        </span>
      </button>
      <Handle className="trace-flow-handle" position={Position.Bottom} type="source" />
    </div>
  );
}

function TraceGroupFlowNode({ data }: NodeProps<TraceGroupFlowNodeType>) {
  const content = (
    <>
      {data.collapsible ? (
        <ChevronDown className={data.collapsed ? "collapsed" : ""} size={17} />
      ) : (
        <span className={`node-dot ${statusTone(data.status)}`} />
      )}
      <span className="trace-flow-group-copy">
        <span className="eyebrow">{data.eyebrow}</span>
        <strong>{data.title}</strong>
        {data.branchLabel ? <small>{data.branchLabel}</small> : null}
      </span>
      <span className="trace-flow-group-state">
        {data.activity ? <span>{labelize(data.activity)}</span> : null}
        <StatusPill status={data.status} />
        <span>{data.nodeCount} nodes</span>
      </span>
    </>
  );

  return (
    <div
      className={`trace-flow-group ${data.collapsed ? "collapsed" : "expanded"} status-${statusTone(data.status)}`}
    >
      <Handle className="trace-flow-handle" position={Position.Top} type="target" />
      {data.collapsible ? (
        <button
          aria-expanded={!data.collapsed}
          className="trace-flow-group-header nodrag"
          onClick={() => data.contextPackId && data.onToggle?.(data.contextPackId)}
          type="button"
        >
          {content}
        </button>
      ) : (
        <div className="trace-flow-group-header">{content}</div>
      )}
      <Handle className="trace-flow-handle" position={Position.Bottom} type="source" />
    </div>
  );
}

const TRACE_NODE_TYPES = {
  traceGroup: TraceGroupFlowNode,
  traceStep: TraceStepFlowNode
};

function TraceFlowCanvas({
  steps,
  artifactsByStepId,
  expandedSuccessfulGroups,
  selectedStepId,
  onSelectStep,
  onToggleGroup
}: {
  steps: AgentStep[];
  artifactsByStepId: ArtifactsByStepId;
  expandedSuccessfulGroups: Set<string>;
  selectedStepId?: string | null;
  onSelectStep?: (stepId: string) => void;
  onToggleGroup: (contextPackId: string) => void;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const [nodes, setNodes] = useState<TraceFlowNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [layouting, setLayouting] = useState(steps.length > 0);
  const [packColumnCount, setPackColumnCount] = useState(2);
  const { fitView } = useReactFlow<TraceFlowNode, Edge>();
  const lastFitSignature = useRef("");
  const structureSignature = useMemo(
    () =>
      steps
        .map(
          (step) =>
            `${step.id}:${step.parent_step_id ?? "root"}:${step.context_pack_id ?? "run"}:${step.status}`
        )
        .join("|") +
      `|expanded:${[...expandedSuccessfulGroups].sort().join(",")}|columns:${packColumnCount}`,
    [expandedSuccessfulGroups, packColumnCount, steps]
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const updateColumnCount = (width: number) => {
      setPackColumnCount(width >= 1420 ? 3 : width >= 760 ? 2 : 1);
    };
    updateColumnCount(canvas.clientWidth);
    const observer = new ResizeObserver((entries) => {
      updateColumnCount(entries[0]?.contentRect.width ?? canvas.clientWidth);
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let active = true;
    let fitFrame = 0;
    if (steps.length === 0) {
      setNodes([]);
      setEdges([]);
      setLayouting(false);
      return () => undefined;
    }
    setLayouting(true);
    void buildTraceFlowElements({
      artifactsByStepId,
      expandedSuccessfulGroups,
      onSelectStep,
      onToggleGroup,
      packColumnCount,
      selectedStepId,
      steps
    }).then((elements) => {
      if (!active) {
        return;
      }
      setNodes(elements.nodes);
      setEdges(elements.edges);
      setLayouting(false);
      if (lastFitSignature.current !== structureSignature) {
        lastFitSignature.current = structureSignature;
        fitFrame = window.requestAnimationFrame(() => {
          void fitView({ duration: 360, maxZoom: 1, padding: 0.14 });
        });
      }
    });
    return () => {
      active = false;
      window.cancelAnimationFrame(fitFrame);
    };
  }, [
    artifactsByStepId,
    expandedSuccessfulGroups,
    fitView,
    onSelectStep,
    onToggleGroup,
    packColumnCount,
    selectedStepId,
    steps,
    structureSignature
  ]);

  return (
    <div className="trace-flow-canvas" ref={canvasRef}>
      <ReactFlow<TraceFlowNode, Edge>
        edges={edges}
        edgesFocusable={false}
        elementsSelectable={false}
        fitView
        maxZoom={1.5}
        minZoom={0.2}
        nodes={nodes}
        nodesConnectable={false}
        nodesDraggable={false}
        nodesFocusable={false}
        nodeTypes={TRACE_NODE_TYPES}
        panOnScroll
        proOptions={{ hideAttribution: false }}
        zoomOnDoubleClick={false}
      >
        <Background color="#b8c7c1" gap={24} size={1.2} variant={BackgroundVariant.Dots} />
        <MiniMap
          nodeColor={(node) => {
            if (node.type === "traceGroup") {
              return "#c8d9d3";
            }
            return statusColor((node.data as TraceStepNodeData).step.status);
          }}
          pannable
          zoomable
        />
        <Controls showInteractive={false} />
      </ReactFlow>
      {layouting ? (
        <div className="trace-flow-layouting">
          <CircleDashed className="spin" size={16} /> Arranging live trace
        </div>
      ) : null}
      {steps.length === 0 ? <div className="trace-flow-empty">No steps</div> : null}
    </div>
  );
}

async function buildTraceFlowElements({
  steps,
  artifactsByStepId,
  expandedSuccessfulGroups,
  selectedStepId,
  onSelectStep,
  onToggleGroup,
  packColumnCount
}: {
  steps: AgentStep[];
  artifactsByStepId: ArtifactsByStepId;
  expandedSuccessfulGroups: Set<string>;
  selectedStepId?: string | null;
  onSelectStep?: (stepId: string) => void;
  onToggleGroup: (contextPackId: string) => void;
  packColumnCount: number;
}) {
  const sortedSteps = [...steps].sort((left, right) => left.sequence - right.sequence);
  const orchestrationSteps = sortedSteps.filter((step) => !step.context_pack_id);
  const contextGroups = groupStepsByContextPack(sortedSteps);
  const collapsedContextIds = new Set(
    contextGroups
      .filter(
        (group) =>
          group.steps.every((step) => step.status === "succeeded") &&
          !expandedSuccessfulGroups.has(group.contextPackId)
      )
      .map((group) => group.contextPackId)
  );
  const layouts = new Map<string, TraceStepLayout>();
  const layoutRequests: Promise<void>[] = [];

  if (orchestrationSteps.length) {
    layoutRequests.push(
      layoutTraceSteps(orchestrationSteps).then((layout) => {
        layouts.set("orchestration", layout);
      })
    );
  }
  contextGroups.forEach((group) => {
    if (!collapsedContextIds.has(group.contextPackId)) {
      layoutRequests.push(
        layoutTraceSteps(group.steps).then((layout) => {
          layouts.set(group.contextPackId, layout);
        })
      );
    }
  });
  await Promise.all(layoutRequests);

  const orchestrationLayout = layouts.get("orchestration") ?? emptyStepLayout();
  const orchestrationBlock: TraceFlowGroupBlock | null = orchestrationSteps.length
    ? {
        collapsed: false,
        data: {
          activity: orchestrationSteps.find((step) => step.status === "running")?.activity ?? null,
          branchLabel: null,
          collapsed: false,
          collapsible: false,
          contextPackId: null,
          eyebrow: "Run-level flow",
          nodeCount: orchestrationSteps.length,
          status: groupStatus(orchestrationSteps),
          title: "Orchestration"
        },
        groupId: "trace-group-orchestration",
        height: orchestrationLayout.height + TRACE_GROUP_HEADER_HEIGHT + 28,
        layout: orchestrationLayout,
        steps: orchestrationSteps,
        width: Math.max(orchestrationLayout.width + 48, TRACE_GROUP_MIN_WIDTH)
      }
    : null;

  const contextBlocks = contextGroups.map<TraceFlowGroupBlock>((group) => {
    const successful = group.steps.every((step) => step.status === "succeeded");
    const collapsed = collapsedContextIds.has(group.contextPackId);
    const runningStep = group.steps.find((step) => step.status === "running");
    const externalParent = findExternalParent(group.steps, sortedSteps);
    const layout = layouts.get(group.contextPackId) ?? emptyStepLayout();
    return {
      collapsed,
      data: {
        activity: runningStep?.activity ?? null,
        branchLabel: externalParent ? `Branches from ${stepNodeTitle(externalParent)}` : null,
        collapsed,
        collapsible: successful,
        contextPackId: group.contextPackId,
        eyebrow: `Context pack · ${shortId(group.contextPackId)}`,
        nodeCount: group.steps.length,
        onToggle: onToggleGroup,
        status: successful ? "succeeded" : groupStatus(group.steps),
        title: contextGroupTitle(group.steps)
      },
      groupId: contextFlowGroupId(group.contextPackId),
      height: collapsed ? 106 : layout.height + TRACE_GROUP_HEADER_HEIGHT + 28,
      layout,
      steps: group.steps,
      width: collapsed ? TRACE_GROUP_MIN_WIDTH : Math.max(layout.width + 48, TRACE_GROUP_MIN_WIDTH)
    };
  });
  const blockPositions = layoutTraceGroupBlocks(
    orchestrationBlock,
    contextBlocks,
    sortedSteps,
    packColumnCount
  );
  const nodes: TraceFlowNode[] = [];

  function appendGroup(block: TraceFlowGroupBlock) {
    const position = blockPositions.get(block.groupId) ?? { x: 24, y: 24 };
    nodes.push({
      id: block.groupId,
      type: "traceGroup",
      position,
      data: block.data,
      selectable: false,
      style: { height: block.height, width: block.width },
      zIndex: 0
    });
    if (block.collapsed) {
      return;
    }
    block.steps.forEach((step) => {
      const stepPosition = block.layout.positions.get(step.id) ?? { x: 0, y: 0 };
      nodes.push({
        id: step.id,
        type: "traceStep",
        data: {
          artifactCount: artifactsByStepId[step.id]?.length ?? 0,
          onSelectStep,
          step
        },
        extent: "parent",
        parentId: block.groupId,
        position: {
          x: stepPosition.x + (block.width - block.layout.width) / 2,
          y: stepPosition.y + TRACE_GROUP_HEADER_HEIGHT
        },
        selected: selectedStepId === step.id,
        style: { height: TRACE_STEP_HEIGHT, width: TRACE_STEP_WIDTH },
        zIndex: 1
      });
    });
  }

  if (orchestrationBlock) {
    appendGroup(orchestrationBlock);
  }
  contextBlocks.forEach(appendGroup);

  const stepById = new Map(sortedSteps.map((step) => [step.id, step]));
  const visibleNodeId = (step: AgentStep) =>
    step.context_pack_id && collapsedContextIds.has(step.context_pack_id)
      ? contextFlowGroupId(step.context_pack_id)
      : step.id;
  const edgeIds = new Set<string>();
  const edges = sortedSteps.flatMap<Edge>((step) => {
    if (!step.parent_step_id) {
      return [];
    }
    const parent = stepById.get(step.parent_step_id);
    if (!parent) {
      return [];
    }
    const source = visibleNodeId(parent);
    const target = visibleNodeId(step);
    const edgeId = `trace-edge-${source}-${target}`;
    if (source === target || edgeIds.has(edgeId)) {
      return [];
    }
    edgeIds.add(edgeId);
    const failed = parent.status === "failed" || step.status === "failed";
    const running = parent.status === "running" || step.status === "running";
    const color = failed ? "#b42318" : running ? "#0f766e" : "#91a39c";
    return [
      {
        id: edgeId,
        animated: running,
        markerEnd: { color, type: MarkerType.ArrowClosed },
        source,
        target,
        type: "smoothstep",
        style: { stroke: color, strokeWidth: running ? 2.2 : 1.6 },
        zIndex: 2
      }
    ];
  });

  return { edges, nodes };
}

function layoutTraceGroupBlocks(
  orchestrationBlock: TraceFlowGroupBlock | null,
  contextBlocks: TraceFlowGroupBlock[],
  steps: AgentStep[],
  requestedColumnCount: number
) {
  const positions = new Map<string, { x: number; y: number }>();
  if (!contextBlocks.length) {
    if (orchestrationBlock) {
      positions.set(orchestrationBlock.groupId, { x: 24, y: 24 });
    }
    return positions;
  }

  const stepById = new Map(steps.map((step) => [step.id, step]));
  const blockByContextId = new Map(
    contextBlocks.flatMap((block) =>
      block.data.contextPackId ? [[block.data.contextPackId, block] as const] : []
    )
  );
  const predecessors = new Map<string, Set<string>>();
  steps.forEach((step) => {
    if (!step.context_pack_id || !step.parent_step_id) {
      return;
    }
    const parentContextId = stepById.get(step.parent_step_id)?.context_pack_id;
    if (
      parentContextId &&
      parentContextId !== step.context_pack_id &&
      blockByContextId.has(parentContextId)
    ) {
      const current = predecessors.get(step.context_pack_id) ?? new Set<string>();
      current.add(parentContextId);
      predecessors.set(step.context_pack_id, current);
    }
  });

  const depthCache = new Map<string, number>();
  function contextDepth(contextPackId: string, visiting = new Set<string>()): number {
    const cached = depthCache.get(contextPackId);
    if (cached !== undefined) {
      return cached;
    }
    if (visiting.has(contextPackId)) {
      return 0;
    }
    const nextVisiting = new Set(visiting).add(contextPackId);
    const depth = Math.max(
      0,
      ...[...(predecessors.get(contextPackId) ?? [])].map(
        (predecessorId) => contextDepth(predecessorId, nextVisiting) + 1
      )
    );
    depthCache.set(contextPackId, depth);
    return depth;
  }

  const blocksByDepth = new Map<number, TraceFlowGroupBlock[]>();
  contextBlocks.forEach((block) => {
    const contextPackId = block.data.contextPackId;
    const depth = contextPackId ? contextDepth(contextPackId) : 0;
    blocksByDepth.set(depth, [...(blocksByDepth.get(depth) ?? []), block]);
  });
  const depthBands = [...blocksByDepth.entries()].sort(([left], [right]) => left - right);
  const widestBand = Math.max(...depthBands.map(([, blocks]) => blocks.length));
  const columnCount = Math.max(1, Math.min(requestedColumnCount, widestBand));
  const columnWidth = Math.max(...contextBlocks.map((block) => block.width));
  const columnGap = 86;
  const packAreaWidth = columnCount * columnWidth + (columnCount - 1) * columnGap;
  const canvasWidth = Math.max(packAreaWidth, orchestrationBlock?.width ?? 0);
  let bandY = 24;

  if (orchestrationBlock) {
    positions.set(orchestrationBlock.groupId, {
      x: (canvasWidth - orchestrationBlock.width) / 2,
      y: bandY
    });
    bandY += orchestrationBlock.height + 144;
  }

  depthBands.forEach(([, unsortedBlocks]) => {
    const blocks = [...unsortedBlocks].sort(
      (left, right) =>
        Math.min(...left.steps.map((step) => step.sequence)) -
        Math.min(...right.steps.map((step) => step.sequence))
    );
    const activeColumnCount = Math.min(columnCount, blocks.length);
    const bandWidth = activeColumnCount * columnWidth + (activeColumnCount - 1) * columnGap;
    const bandX = (canvasWidth - bandWidth) / 2;
    const columnHeights = Array.from({ length: activeColumnCount }, () => 0);

    blocks.forEach((block, index) => {
      const column =
        index < activeColumnCount
          ? index
          : columnHeights.reduce(
              (shortest, height, candidate) =>
                height < columnHeights[shortest] ? candidate : shortest,
              0
            );
      positions.set(block.groupId, {
        x: bandX + column * (columnWidth + columnGap) + (columnWidth - block.width) / 2,
        y: bandY + columnHeights[column]
      });
      columnHeights[column] += block.height + TRACE_GROUP_GAP;
    });
    bandY += Math.max(...columnHeights) + 52;
  });

  return positions;
}

async function layoutTraceSteps(steps: AgentStep[]): Promise<TraceStepLayout> {
  if (!steps.length) {
    return emptyStepLayout();
  }
  const shouldWrap = steps.length > 8;
  const stepIds = new Set(steps.map((step) => step.id));
  const graph = await traceElk.layout({
    id: "trace-layout",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.aspectRatio": shouldWrap ? "0.6" : "0.75",
      "elk.direction": "DOWN",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.spacing.nodeNodeBetweenLayers": "64",
      "elk.layered.wrapping.correctionFactor": "1",
      "elk.layered.wrapping.multiEdge.improveCuts": "true",
      "elk.layered.wrapping.multiEdge.improveWrappedEdges": "true",
      "elk.layered.wrapping.strategy": shouldWrap ? "MULTI_EDGE" : "OFF",
      "elk.padding": "[top=12,left=12,bottom=12,right=12]",
      "elk.spacing.nodeNode": "32"
    },
    children: steps.map((step) => ({
      id: step.id,
      height: TRACE_STEP_HEIGHT,
      width: TRACE_STEP_WIDTH
    })),
    edges: steps.flatMap((step) =>
      step.parent_step_id && stepIds.has(step.parent_step_id)
        ? [
            {
              id: `layout-${step.parent_step_id}-${step.id}`,
              sources: [step.parent_step_id],
              targets: [step.id]
            }
          ]
        : []
    )
  } as ElkNode);
  return {
    height: graph.height ?? TRACE_STEP_HEIGHT,
    positions: new Map(
      (graph.children ?? []).map((child) => [child.id, { x: child.x ?? 0, y: child.y ?? 0 }])
    ),
    width: graph.width ?? TRACE_STEP_WIDTH
  };
}

function emptyStepLayout(): TraceStepLayout {
  return { height: TRACE_STEP_HEIGHT, positions: new Map(), width: TRACE_STEP_WIDTH };
}

function contextFlowGroupId(contextPackId: string) {
  return `trace-group-context-${contextPackId}`;
}

function statusColor(status: string) {
  const tone = statusTone(status);
  if (tone === "ok") {
    return "#167044";
  }
  if (tone === "warn") {
    return "#a16207";
  }
  if (tone === "bad") {
    return "#b42318";
  }
  return "#6254a4";
}

function RepositorySettingsPage({ repositoryId }: { repositoryId: string }) {
  const repository = useAsyncData(() => api.getRepository(repositoryId), [repositoryId]);
  const profiles = useAsyncData(() => api.listAttentionProfiles(repositoryId), [repositoryId]);
  const schedule = useAsyncData(() => api.getRepositorySchedule(repositoryId), [repositoryId]);
  const runtime = useAsyncData(api.getRuntime, []);

  const [profileName, setProfileName] = useState("");
  const [defaultWeight, setDefaultWeight] = useState("1");
  const [activateNewProfile, setActivateNewProfile] = useState(true);
  const [focusAreas, setFocusAreas] = useState<FocusAreaDraft[]>([newFocusAreaDraft()]);
  const [profileBusy, setProfileBusy] = useState(false);
  const [activatingProfileId, setActivatingProfileId] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileNotice, setProfileNotice] = useState<string | null>(null);

  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [thresholdMode, setThresholdMode] = useState<"preset" | "advanced">("preset");
  const [presetScore, setPresetScore] = useState<DriftPresetScore>("25");
  const [minimumScore, setMinimumScore] = useState("25");
  const [maximumScore, setMaximumScore] = useState("");
  const [scheduledPackTypes, setScheduledPackTypes] = useState<string[]>([
    "low_level_components"
  ]);
  const [scheduledQuestionCounts, setScheduledQuestionCounts] = useState<
    Record<string, number>
  >({
    low_level_components: 1
  });
  const [maxQuestionsPerTrigger, setMaxQuestionsPerTrigger] = useState("15");
  const [scheduleBusy, setScheduleBusy] = useState(false);
  const [scheduleError, setScheduleError] = useState<string | null>(null);
  const [savedSchedule, setSavedSchedule] = useState<RepositorySchedule | null>(null);
  const defaultQuestionCount = runtime.data?.generation_questions_per_category ?? 1;
  const maxQuestionsPerCategory =
    runtime.data?.generation_max_questions_per_category ?? defaultQuestionCount;
  const maxQuestionsPerJob =
    runtime.data?.generation_max_questions_per_job ?? maxQuestionsPerCategory;
  const scheduledQuestionTotal = scheduledPackTypes.reduce(
    (total, packType) =>
      total + (scheduledQuestionCounts[packType] ?? defaultQuestionCount),
    0
  );
  const parsedQuestionLimit = Number(maxQuestionsPerTrigger);
  const parsedMinimumScore = Number(minimumScore);
  const parsedMaximumScore = maximumScore.trim() ? Number(maximumScore) : null;
  const driftRangeIsValid =
    thresholdMode === "preset" ||
    (Number.isFinite(parsedMinimumScore) &&
      parsedMinimumScore >= 0 &&
      (parsedMaximumScore === null ||
        (Number.isFinite(parsedMaximumScore) &&
          parsedMaximumScore >= parsedMinimumScore)));
  const generationMixIsValid =
    scheduledPackTypes.length > 0 &&
    Number.isInteger(parsedQuestionLimit) &&
    parsedQuestionLimit >= 1 &&
    parsedQuestionLimit <= maxQuestionsPerJob &&
    scheduledQuestionTotal <= parsedQuestionLimit;

  useEffect(() => {
    if (!schedule.data) {
      return;
    }
    setScheduleEnabled(schedule.data.enabled);
    setMinimumScore(String(schedule.data.drift_min_score));
    setMaximumScore(
      schedule.data.drift_max_score === null ? "" : String(schedule.data.drift_max_score)
    );
    const matchingPreset = DRIFT_PRESETS.find(
      (preset) =>
        Number(preset.score) === Number(schedule.data?.drift_min_score) &&
        schedule.data?.drift_max_score === null
    );
    if (matchingPreset) {
      setThresholdMode("preset");
      setPresetScore(matchingPreset.score);
    } else {
      setThresholdMode("advanced");
    }
    const generationPlan =
      schedule.data.generation_plan.length > 0
        ? schedule.data.generation_plan
        : schedule.data.pack_types.map((category) => ({
            category,
            question_count: defaultQuestionCount
          }));
    setScheduledPackTypes(generationPlan.map((item) => item.category));
    setScheduledQuestionCounts(
      Object.fromEntries(
        generationPlan.map((item) => [item.category, item.question_count])
      )
    );
    setMaxQuestionsPerTrigger(String(schedule.data.max_questions_per_trigger));
    setSavedSchedule(schedule.data);
  }, [defaultQuestionCount, schedule.data]);

  useEffect(() => {
    if (schedule.data) {
      return;
    }
    setScheduledQuestionCounts((current) => ({
      ...current,
      low_level_components: defaultQuestionCount
    }));
    setMaxQuestionsPerTrigger(String(maxQuestionsPerJob));
  }, [defaultQuestionCount, maxQuestionsPerJob, schedule.data]);

  async function createProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setProfileBusy(true);
    setProfileError(null);
    setProfileNotice(null);
    try {
      const created = await api.createAttentionProfile(repositoryId, {
        name: profileName.trim(),
        default_weight: defaultWeight,
        active: activateNewProfile,
        focus_areas: focusAreas.map((area) => ({
          name: area.name.trim(),
          description: area.description.trim() || null,
          weight: area.weight,
          path_globs: area.pathGlobs
            .split(/[,\n]/)
            .map((pathGlob) => pathGlob.trim())
            .filter(Boolean)
        }))
      });
      setProfileName("");
      setDefaultWeight("1");
      setActivateNewProfile(true);
      setFocusAreas([newFocusAreaDraft()]);
      setProfileNotice(
        `${created.name} was created${created.active ? " and activated" : ""}.`
      );
      await profiles.reload();
    } catch (exc) {
      setProfileError(errorMessage(exc));
    } finally {
      setProfileBusy(false);
    }
  }

  async function activateProfile(profile: AttentionProfile) {
    setActivatingProfileId(profile.id);
    setProfileError(null);
    setProfileNotice(null);
    try {
      await api.activateAttentionProfile(repositoryId, profile.id);
      setProfileNotice(`${profile.name} is now the active attention profile.`);
      await profiles.reload();
    } catch (exc) {
      setProfileError(errorMessage(exc));
    } finally {
      setActivatingProfileId(null);
    }
  }

  function updateFocusArea(
    key: string,
    field: keyof Omit<FocusAreaDraft, "key">,
    value: string
  ) {
    setFocusAreas((current) =>
      current.map((area) => (area.key === key ? { ...area, [field]: value } : area))
    );
  }

  async function saveSchedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setScheduleBusy(true);
    setScheduleError(null);
    try {
      const updated = await api.updateRepositorySchedule(repositoryId, {
        enabled: scheduleEnabled,
        drift_min_score: thresholdMode === "preset" ? presetScore : minimumScore,
        drift_max_score:
          thresholdMode === "preset"
            ? null
            : maximumScore.trim()
              ? maximumScore
              : null,
        generation_plan: scheduledPackTypes.map((category) => ({
          category,
          question_count:
            scheduledQuestionCounts[category] ?? defaultQuestionCount
        })),
        max_questions_per_trigger: parsedQuestionLimit
      });
      setSavedSchedule(updated);
      await schedule.reload();
    } catch (exc) {
      setScheduleError(errorMessage(exc));
    } finally {
      setScheduleBusy(false);
    }
  }

  return (
    <div className="page stack">
      <PageTitle
        icon={<Settings size={22} />}
        title={`${repository.data?.name ?? "Repository"} settings`}
        eyebrow="Automation and attention"
        actions={
          <a className="icon-link" href={`#/repositories/${repositoryId}`}>
            <ChevronLeft size={16} />
            Back to repository
          </a>
        }
      />
      {repository.error ? (
        <InlineError message={`Repository details unavailable: ${repository.error}`} />
      ) : null}
      {runtime.error ? (
        <InlineError message={`Generation limits unavailable: ${runtime.error}`} />
      ) : null}

      <section className="panel">
        <PanelHeader title="Attention profiles" icon={<Layers size={17} />} />
        <div className="settings-split">
          <form className="stack settings-form" onSubmit={createProfile}>
            <div>
              <h3>Create an attention profile</h3>
              <p className="muted settings-intro">
                Weight the repository areas that should matter most when drift is scored and
                questions are generated.
              </p>
            </div>
            <div className="two-column">
              <label>
                <span>Profile name</span>
                <input
                  maxLength={200}
                  onChange={(event) => setProfileName(event.target.value)}
                  placeholder="Backend focus"
                  required
                  value={profileName}
                />
              </label>
              <label>
                <span>Default repository weight</span>
                <input
                  max="10"
                  min="0"
                  onChange={(event) => setDefaultWeight(event.target.value)}
                  required
                  step="0.1"
                  type="number"
                  value={defaultWeight}
                />
              </label>
            </div>
            <div className="focus-area-list">
              <div className="settings-section-heading">
                <div>
                  <h3>Focus areas</h3>
                  <span className="muted">Path globs can be comma or line separated.</span>
                </div>
                <button
                  onClick={() =>
                    setFocusAreas((current) => [...current, newFocusAreaDraft()])
                  }
                  type="button"
                >
                  <Plus size={14} />
                  Add area
                </button>
              </div>
              {focusAreas.map((area, index) => (
                <fieldset className="focus-area-card" key={area.key}>
                  <legend>Area {index + 1}</legend>
                  <div className="focus-area-heading">
                    <span className="muted">Files matching these globs receive this weight.</span>
                    <IconButton
                      disabled={focusAreas.length === 1}
                      label={`Remove focus area ${index + 1}`}
                      onClick={() =>
                        setFocusAreas((current) =>
                          current.filter((candidate) => candidate.key !== area.key)
                        )
                      }
                    >
                      <X size={14} />
                    </IconButton>
                  </div>
                  <div className="two-column">
                    <label>
                      <span>Area name</span>
                      <input
                        maxLength={200}
                        onChange={(event) =>
                          updateFocusArea(area.key, "name", event.target.value)
                        }
                        placeholder="API and services"
                        required
                        value={area.name}
                      />
                    </label>
                    <label>
                      <span>Weight</span>
                      <input
                        max="10"
                        min="0"
                        onChange={(event) =>
                          updateFocusArea(area.key, "weight", event.target.value)
                        }
                        required
                        step="0.1"
                        type="number"
                        value={area.weight}
                      />
                    </label>
                  </div>
                  <label>
                    <span>Description (optional)</span>
                    <input
                      maxLength={1000}
                      onChange={(event) =>
                        updateFocusArea(area.key, "description", event.target.value)
                      }
                      placeholder="Core request handling and business logic"
                      value={area.description}
                    />
                  </label>
                  <label>
                    <span>Path globs</span>
                    <textarea
                      onChange={(event) =>
                        updateFocusArea(area.key, "pathGlobs", event.target.value)
                      }
                      placeholder={"src/ooh/api/**\nsrc/ooh/services/**"}
                      required
                      rows={2}
                      value={area.pathGlobs}
                    />
                  </label>
                </fieldset>
              ))}
            </div>
            <label className="row checkbox-label">
              <input
                checked={activateNewProfile}
                onChange={(event) => setActivateNewProfile(event.target.checked)}
                type="checkbox"
              />
              <span>Make this the active profile immediately</span>
            </label>
            {profileError ? <InlineError message={profileError} /> : null}
            {profileNotice ? (
              <div className="notice">
                <CheckCircle2 size={15} />
                {profileNotice}
              </div>
            ) : null}
            <button
              className="primary action-button"
              disabled={profileBusy}
              type="submit"
            >
              <Plus size={15} />
              {profileBusy ? "Creating…" : "Create profile"}
            </button>
          </form>

          <div className="profile-library">
            <div>
              <h3>Saved profiles</h3>
              <p className="muted settings-intro">
                One profile is active at a time. Activation affects the next repository
                ingestion.
              </p>
            </div>
            <AsyncBoundary state={profiles}>
              <div className="profile-list">
                {(profiles.data ?? []).map((profile: AttentionProfile) => (
                  <article
                    className={`profile-card ${profile.active ? "active" : ""}`}
                    key={profile.id}
                  >
                    <div className="item-card-header">
                      <div>
                        <strong>{profile.name}</strong>
                        <span className="muted">
                          Default weight {String(profile.default_weight)}
                        </span>
                      </div>
                      <StatusPill status={profile.active ? "active" : "saved"} />
                    </div>
                    <div className="profile-focus-areas">
                      {profile.focus_areas.map((area) => (
                        <div className="profile-focus-area" key={area.id}>
                          <div className="item-card-header">
                            <strong>{area.name}</strong>
                            <span className="weight-badge">×{String(area.weight)}</span>
                          </div>
                          {area.description ? (
                            <span className="muted">{area.description}</span>
                          ) : null}
                          <code>{area.path_globs.join(", ")}</code>
                        </div>
                      ))}
                      {profile.focus_areas.length === 0 ? (
                        <span className="muted">Uses the default weight for every path.</span>
                      ) : null}
                    </div>
                    {!profile.active ? (
                      <button
                        disabled={activatingProfileId !== null}
                        onClick={() => void activateProfile(profile)}
                        type="button"
                      >
                        <CheckCircle2 size={14} />
                        {activatingProfileId === profile.id ? "Activating…" : "Make active"}
                      </button>
                    ) : null}
                  </article>
                ))}
                {profiles.data?.length === 0 ? (
                  <div className="empty-state">No attention profiles yet</div>
                ) : null}
              </div>
            </AsyncBoundary>
          </div>
        </div>
      </section>

      <section className="panel">
        <PanelHeader title="Drift-triggered tests" icon={<Activity size={17} />} />
        <form className="stack schedule-form" onSubmit={saveSchedule}>
          <div className="automation-toggle">
            <div>
              <strong>Automatic test generation</strong>
              <span className="muted">
                Evaluate new, non-baseline drift events against this policy.
              </span>
            </div>
            <label className="switch">
              <input
                checked={scheduleEnabled}
                onChange={(event) => setScheduleEnabled(event.target.checked)}
                type="checkbox"
              />
              <span aria-hidden="true" />
              <em>{scheduleEnabled ? "Enabled" : "Disabled"}</em>
            </label>
          </div>

          <div className="settings-block">
            <div className="settings-section-heading">
              <div>
                <h3>Generate tests when drift is at least</h3>
                <span className="muted">
                  A minimum level includes every more severe drift event.
                </span>
              </div>
              <button
                className={thresholdMode === "advanced" ? "active" : ""}
                onClick={() =>
                  setThresholdMode((current) =>
                    current === "advanced" ? "preset" : "advanced"
                  )
                }
                type="button"
              >
                {thresholdMode === "advanced" ? "Use presets" : "Exact range"}
              </button>
            </div>
            {thresholdMode === "preset" ? (
              <div className="threshold-grid">
                {DRIFT_PRESETS.map((preset) => (
                  <button
                    className={presetScore === preset.score ? "active" : ""}
                    key={preset.score}
                    onClick={() => {
                      setPresetScore(preset.score);
                      setMinimumScore(preset.score);
                      setMaximumScore("");
                    }}
                    type="button"
                  >
                    <strong>{preset.name}</strong>
                    <span>{preset.description}</span>
                    <code>score ≥ {preset.score}</code>
                  </button>
                ))}
              </div>
            ) : (
              <div className="advanced-range">
                <p className="notice">
                  This exact band differs from a minimum level: scores above the maximum will
                  not trigger generation.
                </p>
                <div className="two-column">
                  <label>
                    <span>Minimum score (inclusive)</span>
                    <input
                      min="0"
                      onChange={(event) => setMinimumScore(event.target.value)}
                      required
                      step="0.01"
                      type="number"
                      value={minimumScore}
                    />
                  </label>
                  <label>
                    <span>Maximum score (inclusive, optional)</span>
                    <input
                      min="0"
                      onChange={(event) => setMaximumScore(event.target.value)}
                      step="0.01"
                      type="number"
                      value={maximumScore}
                    />
                  </label>
                </div>
                {!driftRangeIsValid ? (
                  <InlineError
                    message="The maximum score must be greater than or equal to the minimum score."
                  />
                ) : null}
              </div>
            )}
          </div>

          <div className="settings-block">
            <div>
              <h3>Generation mix</h3>
              <p className="muted settings-intro">
                Choose categories and how many independent questions each trigger should queue.
              </p>
            </div>
            <div className="generation-mix-list">
              {PACK_TYPES.map((packType) => {
                const selected = scheduledPackTypes.includes(packType);
                return (
                  <div className={`generation-mix-row ${selected ? "selected" : ""}`} key={packType}>
                    <label className="row checkbox-label">
                      <input
                        checked={selected}
                        onChange={() =>
                          setScheduledPackTypes((current) =>
                            current.includes(packType)
                              ? current.filter((value) => value !== packType)
                              : [...current, packType]
                          )
                        }
                        type="checkbox"
                      />
                      <span>{labelize(packType)}</span>
                    </label>
                    <label>
                      <span>Questions</span>
                      <input
                        disabled={!selected}
                        max={maxQuestionsPerCategory}
                        min="1"
                        onChange={(event) =>
                          setScheduledQuestionCounts((current) => ({
                            ...current,
                            [packType]: Math.min(
                              maxQuestionsPerCategory,
                              Math.max(
                                1,
                                Number(event.target.value) || defaultQuestionCount
                              )
                            )
                          }))
                        }
                        type="number"
                        value={
                          scheduledQuestionCounts[packType] ?? defaultQuestionCount
                        }
                      />
                    </label>
                  </div>
                );
              })}
            </div>
            <div className="generation-limit">
              <div>
                <strong>Overall maximum per trigger</strong>
                <span className="muted">
                  A durable safety cap stored with this repository policy.
                </span>
              </div>
              <label>
                <span>Maximum questions</span>
                <input
                  max={maxQuestionsPerJob}
                  min="1"
                  onChange={(event) => setMaxQuestionsPerTrigger(event.target.value)}
                  required
                  type="number"
                  value={maxQuestionsPerTrigger}
                />
              </label>
              <span className={generationMixIsValid ? "question-total" : "question-total bad"}>
                {scheduledQuestionTotal} planned / {maxQuestionsPerTrigger || "—"} max
              </span>
            </div>
            {!generationMixIsValid ? (
              <InlineError
                message={
                  scheduledPackTypes.length === 0
                    ? "Select at least one test category."
                    : `The planned question count must fit inside a 1–${maxQuestionsPerJob} question trigger limit.`
                }
              />
            ) : null}
          </div>

          <p className="muted">
            Settings are copied into each triggered job, so later edits do not change work that
            is already queued.
          </p>
          {scheduleError ? <InlineError message={scheduleError} /> : null}
          {savedSchedule ? (
            <div className="notice">
              <Clock3 size={15} />
              Active since {formatDate(savedSchedule.active_since)}
            </div>
          ) : null}
          <button
            className="primary action-button"
            disabled={
              scheduleBusy ||
              runtime.loading ||
              runtime.error !== null ||
              !driftRangeIsValid ||
              !generationMixIsValid
            }
            type="submit"
          >
            <Settings size={15} />
            {scheduleBusy ? "Saving…" : "Save repository settings"}
          </button>
        </form>
      </section>
    </div>
  );
}

function JobPage({ jobId }: { jobId: string }) {
  const job = useAsyncData(() => api.getJob(jobId), [jobId]);

  return (
    <div className="page stack">
      <PageTitle icon={<Clock3 size={22} />} title="Job detail" eyebrow={shortId(jobId)} />
      <AsyncBoundary state={job}>
        {job.data ? (
          <div className="two-column">
            <section className="panel">
              <PanelHeader title={labelize(job.data.job_type)} icon={<Clock3 size={17} />} />
              <dl className="definition-grid">
                <dt>Status</dt>
                <dd>
                  <StatusPill status={job.data.status} />
                </dd>
                <dt>Attempts</dt>
                <dd>
                  {job.data.attempt_count} / {job.data.max_attempts}
                </dd>
                <dt>Run after</dt>
                <dd>{formatDate(job.data.run_after)}</dd>
                <dt>Locked by</dt>
                <dd>{job.data.locked_by ?? "-"}</dd>
                <dt>Lease expires</dt>
                <dd>{formatDate(job.data.lease_expires_at)}</dd>
                <dt>Idempotency key</dt>
                <dd>{job.data.idempotency_key ?? "-"}</dd>
                <dt>Error</dt>
                <dd>{job.data.error_summary ?? "-"}</dd>
              </dl>
            </section>
            <section className="panel">
              <PanelHeader title="Payload" icon={<Package size={17} />} />
              <JsonBlock payload={job.data.payload} />
            </section>
            <section className="panel">
              <PanelHeader title="Result metadata" icon={<Database size={17} />} />
              <JsonBlock payload={job.data.result_metadata} />
            </section>
          </div>
        ) : null}
      </AsyncBoundary>
    </div>
  );
}

function GeneratedTestPage({
  generatedTestId,
  repositoryId
}: {
  generatedTestId: string;
  repositoryId: string | null;
}) {
  const tests = useAsyncData(
    () => (repositoryId ? api.listGeneratedTests(repositoryId) : Promise.resolve([])),
    [repositoryId]
  );
  const selectedTest = tests.data?.find((item) => item.id === generatedTestId) ?? null;
  const sessionTests = useMemo(() => {
    if (!selectedTest) {
      return [];
    }
    const siblings = selectedTest.agent_run_id
      ? (tests.data ?? []).filter((item) => item.agent_run_id === selectedTest.agent_run_id)
      : [selectedTest];
    return [...siblings].sort(
      (left, right) =>
        left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id)
    );
  }, [selectedTest, tests.data]);
  const sessionKey = sessionTests.map((test) => test.id).join(",");
  const progress = useAsyncData<TestSessionProgress>(async () => {
    const entries = await Promise.all(
      sessionTests.map(async (test) => {
        const [answers, results] = await Promise.all([
          api.listAnswers(test.id),
          api.listResults(test.id)
        ]);
        return [test.id, { answers, results }] as const;
      })
    );
    return Object.fromEntries(entries);
  }, [sessionKey]);
  const [activeTestId, setActiveTestId] = useState(generatedTestId);
  const [drafts, setDrafts] = useState<Record<string, AnswerDraft>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [judgeJobs, setJudgeJobs] = useState<Record<string, Job>>({});
  const [submitting, setSubmitting] = useState(false);
  const test =
    sessionTests.find((item) => item.id === activeTestId) ??
    sessionTests.find((item) => item.id === generatedTestId) ??
    null;
  const currentIndex = test ? sessionTests.findIndex((item) => item.id === test.id) : -1;
  const currentProgress = test ? progress.data?.[test.id] : null;
  const submittedAnswer = currentProgress?.answers[0] ?? null;
  const draft =
    (test ? drafts[test.id] : null) ??
    draftFromSubmittedAnswer(submittedAnswer) ??
    EMPTY_ANSWER_DRAFT;
  const latestResult = currentProgress?.results[0] ?? null;
  const judgeJob = test ? judgeJobs[test.id] ?? null : null;
  const answerPayload = test ? answerPayloadFromDraft(test, draft) : null;
  const alreadySubmitted = Boolean(submittedAnswer);

  useEffect(() => {
    setActiveTestId(generatedTestId);
    setSubmitError(null);
  }, [generatedTestId]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!test || !answerPayload || alreadySubmitted) {
      return;
    }
    setSubmitError(null);
    setSubmitting(true);
    try {
      const result = await api.submitAnswer(test.id, answerPayload);
      setJudgeJobs((current) => ({ ...current, [test.id]: result.judge_job }));
      await progress.reload();
    } catch (exc) {
      setSubmitError(errorMessage(exc));
    } finally {
      setSubmitting(false);
    }
  }

  function updateDraft(nextDraft: AnswerDraft) {
    if (!test) {
      return;
    }
    setDrafts((current) => ({ ...current, [test.id]: nextDraft }));
  }

  function goToQuestion(index: number) {
    const nextTest = sessionTests[index];
    if (!nextTest) {
      return;
    }
    setActiveTestId(nextTest.id);
    setSubmitError(null);
    navigate(
      `/generated-tests/${nextTest.id}${
        repositoryId ? `?repositoryId=${encodeURIComponent(repositoryId)}` : ""
      }`
    );
  }

  return (
    <div className="page stack">
      <PageTitle
        icon={<FileText size={22} />}
        title="Test session"
        eyebrow={
          currentIndex >= 0
            ? `Question ${currentIndex + 1} of ${sessionTests.length}`
            : shortId(generatedTestId)
        }
      />
      <section className="panel test-progress-panel">
        <div className="test-progress-header">
          <div>
            <strong>{sessionTests.length || 1} question test set</strong>
            <span className="muted">
              {selectedTest?.agent_run_id
                ? `Agent run ${shortId(selectedTest.agent_run_id)}`
                : "Single generated question"}
            </span>
          </div>
          <button onClick={() => void progress.reload()} type="button">
            <RefreshCw size={14} className={progress.loading ? "spin" : undefined} />
            Refresh results
          </button>
        </div>
        <div className="question-progress" aria-label="Test question progress">
          {sessionTests.map((sessionTest, index) => {
            const state = testSessionState(
              progress.data?.[sessionTest.id],
              drafts[sessionTest.id]
            );
            return (
              <button
                aria-current={sessionTest.id === test?.id ? "step" : undefined}
                className={`question-progress-step ${
                  sessionTest.id === test?.id ? "active" : ""
                } ${state}`}
                key={sessionTest.id}
                onClick={() => goToQuestion(index)}
                type="button"
              >
                <span>{index + 1}</span>
                <small>{labelize(state)}</small>
              </button>
            );
          })}
        </div>
      </section>

      <div className="test-session-layout">
        <section className="panel">
          <PanelHeader
            title={test ? labelize(test.category) : "Question"}
            icon={<FileText size={17} />}
          />
          {test ? (
            <div className="question-surface">
              <div className="question-meta">
                <StatusPill status={labelize(test.presentation_payload.type)} />
                <span className="muted">
                  Question {currentIndex + 1} of {sessionTests.length}
                </span>
              </div>
              <h2 className="question-copy">{test.presentation_payload.question}</h2>
              <form className="test-answer-form" onSubmit={onSubmit}>
                {test.presentation_payload.type === "short_answer" ? (
                  <label>
                    Your answer
                    <textarea
                      disabled={alreadySubmitted}
                      placeholder="Write a concise answer supported by the repository context."
                      required
                      value={draft.responseText}
                      onChange={(event) =>
                        updateDraft({ ...draft, responseText: event.target.value })
                      }
                    />
                  </label>
                ) : (
                  <fieldset className="answer-options" disabled={alreadySubmitted}>
                    <legend>
                      {test.presentation_payload.type === "mcq_single"
                        ? "Choose one answer"
                        : "Choose all that apply"}
                    </legend>
                    {test.presentation_payload.options.map((option) => {
                      const isSingle = test.presentation_payload.type === "mcq_single";
                      const checked = isSingle
                        ? draft.selectedOptionId === option.id
                        : draft.selectedOptionIds.includes(option.id);
                      return (
                        <label className="answer-option" key={option.id}>
                          <input
                            checked={checked}
                            name={`answer-${test.id}`}
                            onChange={(event) => {
                              if (isSingle) {
                                updateDraft({ ...draft, selectedOptionId: option.id });
                                return;
                              }
                              const selectedOptionIds = event.target.checked
                                ? [...draft.selectedOptionIds, option.id]
                                : draft.selectedOptionIds.filter(
                                    (optionId) => optionId !== option.id
                                  );
                              updateDraft({ ...draft, selectedOptionIds });
                            }}
                            type={isSingle ? "radio" : "checkbox"}
                          />
                          <span className="option-id">{option.id}</span>
                          <span>{option.text}</span>
                        </label>
                      );
                    })}
                  </fieldset>
                )}
                {submitError ? <InlineError message={submitError} /> : null}
                {alreadySubmitted ? (
                  <div className="notice">
                    <CheckCircle2 size={15} />
                    Answer submitted. Results appear after the judge job completes.
                  </div>
                ) : null}
                <div className="question-actions">
                  <button
                    disabled={currentIndex <= 0}
                    onClick={() => goToQuestion(currentIndex - 1)}
                    type="button"
                  >
                    <ChevronLeft size={15} />
                    Previous
                  </button>
                  <button
                    className="primary action-button"
                    disabled={!answerPayload || alreadySubmitted || submitting}
                    type="submit"
                  >
                    {submitting ? <CircleDashed className="spin" size={15} /> : <Send size={15} />}
                    {alreadySubmitted ? "Submitted" : "Submit answer"}
                  </button>
                  <button
                    disabled={currentIndex < 0 || currentIndex >= sessionTests.length - 1}
                    onClick={() => goToQuestion(currentIndex + 1)}
                    type="button"
                  >
                    Next
                    <ChevronRight size={15} />
                  </button>
                </div>
              </form>
            </div>
          ) : (
            <AsyncBoundary state={tests}>
              <div className="empty-state">
                {repositoryId
                  ? "Generated test unavailable"
                  : "Open this test from its repository to load the complete test set."}
              </div>
            </AsyncBoundary>
          )}
        </section>

        <section className="panel">
          <PanelHeader title="Result" icon={<CheckCircle2 size={17} />} />
          {judgeJob ? (
            <div className="notice">
              <Clock3 size={15} />
              <a href={`#/jobs/${judgeJob.id}`}>{labelize(judgeJob.job_type)}</a>
              <StatusPill status={judgeJob.status} />
            </div>
          ) : null}
          <AsyncBoundary state={progress}>
            {latestResult ? (
              <TestResultSummary result={latestResult} test={test} />
            ) : (
              <div className="empty-state result-empty">
                {alreadySubmitted
                  ? "The answer is complete and waiting to be judged. Refresh when the judge job finishes."
                  : "Submit an answer to receive a score, feedback, and answer guidance."}
              </div>
            )}
          </AsyncBoundary>
        </section>
      </div>

      {test ? (
        <section className="panel test-inspection">
          <details>
            <summary>Evidence and provenance</summary>
            <div className="inspection-content">
              {test.agent_run_id ? (
                <a className="inline-link" href={`#/agent-runs/${test.agent_run_id}`}>
                  <Network size={14} />
                  Open generation trace
                </a>
              ) : null}
              <div className="source-list">
                {test.presentation_payload.evidence_refs.map((evidence, index) => (
                  <div className="evidence-row" key={`${evidence.source_uri}-${index}`}>
                    <strong>{evidence.source_type}</strong>
                    <span>{evidence.source_uri}</span>
                    {evidence.content_hash ? <code>{shortSha(evidence.content_hash)}</code> : null}
                  </div>
                ))}
                {test.presentation_payload.evidence_refs.length === 0 ? (
                  <span className="muted">No evidence references were attached.</span>
                ) : null}
              </div>
            </div>
          </details>
          <details>
            <summary>Advanced JSON inspection</summary>
            <div className="inspection-content">
              <PanelSubhead title="Public presentation payload" />
              <JsonBlock payload={test.presentation_payload} />
              {submittedAnswer ? (
                <>
                  <PanelSubhead title="Submitted answer payload" />
                  <JsonBlock payload={submittedAnswer.answer_payload} />
                </>
              ) : null}
              {latestResult ? (
                <>
                  <PanelSubhead title="Judged result payload" />
                  <JsonBlock payload={latestResult} />
                </>
              ) : null}
            </div>
          </details>
        </section>
      ) : null}
    </div>
  );
}

function TestResultSummary({
  result,
  test
}: {
  result: TestResult;
  test: GeneratedTest | null;
}) {
  const feedback =
    typeof result.feedback.feedback === "string" ? result.feedback.feedback : null;
  const missedConcepts = Array.isArray(result.feedback.missed_concepts)
    ? result.feedback.missed_concepts.filter(
        (concept): concept is string => typeof concept === "string"
      )
    : [];
  const guidance = result.grading_guidance;
  const correctOptions =
    guidance && guidance.type !== "short_answer" && test?.presentation_payload.type !== "short_answer"
      ? test?.presentation_payload.options.filter((option) =>
          guidance.correct_option_ids.includes(option.id)
        )
      : [];

  return (
    <div className="result-summary">
      <div className="result-score">
        <div>
          <span>Score</span>
          <strong>{formatScore(result.score)}</strong>
        </div>
        <StatusPill status={result.status} />
      </div>
      {feedback ? (
        <div className="feedback-block">
          <PanelSubhead title="Judge feedback" />
          <p>{feedback}</p>
        </div>
      ) : null}
      {missedConcepts.length ? (
        <div className="feedback-block">
          <PanelSubhead title="Missed concepts" />
          <ul>
            {missedConcepts.map((concept) => (
              <li key={concept}>{concept}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {guidance ? (
        <div className="guidance-block">
          <PanelSubhead title="Answer guidance" />
          {guidance.type === "short_answer" ? (
            <p>{guidance.expected_answer}</p>
          ) : (
            <ul>
              {(correctOptions ?? []).map((option) => (
                <li key={option.id}>
                  <strong>{option.id}</strong> — {option.text}
                </li>
              ))}
            </ul>
          )}
          {guidance.explanation ? <p className="muted">{guidance.explanation}</p> : null}
          {guidance.rubric.length ? (
            <details className="rubric-details">
              <summary>View grading rubric</summary>
              <ul>
                {guidance.rubric.map((criterion) => (
                  <li key={criterion.criterion}>
                    <strong>{criterion.criterion}</strong>: {criterion.description}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function AgentRunPage({ agentRunId }: { agentRunId: string }) {
  const traceData = useTraceData(agentRunId);
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const selectedStep = traceData.steps.find((step) => step.id === selectedStepId) ?? null;
  const artifacts = selectedStep ? traceData.artifactsByStepId[selectedStep.id] ?? [] : [];
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const selectedArtifact =
    artifacts.find((artifact) => artifact.id === selectedArtifactId) ?? artifacts[0] ?? null;
  const provenance = useAsyncData(
    () => (selectedArtifact ? api.listArtifactProvenance(selectedArtifact.id) : Promise.resolve([])),
    [selectedArtifact?.id]
  );

  useEffect(() => {
    setSelectedArtifactId(null);
  }, [selectedStepId]);

  useEffect(() => {
    if (!selectedStepId) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelectedStepId(null);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedStepId]);

  useEffect(() => {
    if (
      selectedArtifactId &&
      !artifacts.some((artifact) => artifact.id === selectedArtifactId)
    ) {
      setSelectedArtifactId(null);
    }
  }, [artifacts, selectedArtifactId]);

  const modelCallCount = traceData.steps.filter((step) => step.step_type === "model_call").length;

  return (
    <div className="page stack">
      <PageTitle
        icon={<Network size={22} />}
        title={traceData.run ? labelize(traceData.run.run_type) : "Agent run"}
        eyebrow={shortId(agentRunId)}
      />
      {traceData.error && !traceData.run ? <InlineError message={traceData.error} /> : null}
      {traceData.run ? (
        <div className="metric-grid">
          <Metric
            label="Status"
            value={labelize(traceData.run.status)}
            tone={statusTone(traceData.run.status)}
          />
          <Metric label="Model" value={traceData.run.model_profile ?? "-"} />
          <Metric
            label="Duration"
            value={formatLiveDuration(traceData.run.started_at, traceData.run.finished_at)}
          />
          <Metric label="LLM calls" value={modelCallCount} tone="neutral" />
        </div>
      ) : null}

      <section className="panel">
        <PanelHeader title="Live execution graph" icon={<Network size={17} />} />
        <TracePreview
          artifactsByStepId={traceData.artifactsByStepId}
          connection={traceData.connection}
          error={traceData.error}
          loading={traceData.loading}
          onSelectStep={(stepId) => setSelectedStepId(stepId)}
          selectedStepId={selectedStepId}
          steps={traceData.steps}
        />
      </section>

      {selectedStep ? (
        <TraceDetailDrawer
          artifacts={artifacts}
          onClose={() => setSelectedStepId(null)}
          onSelectArtifact={setSelectedArtifactId}
          provenance={provenance}
          selectedArtifact={selectedArtifact}
          step={selectedStep}
        />
      ) : null}
    </div>
  );
}

function TraceDetailDrawer({
  step,
  artifacts,
  selectedArtifact,
  provenance,
  onSelectArtifact,
  onClose
}: {
  step: AgentStep;
  artifacts: AgentArtifact[];
  selectedArtifact: AgentArtifact | null;
  provenance: AsyncState<ProvenanceRef[]> & { reload: () => Promise<void> };
  onSelectArtifact: (artifactId: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="trace-drawer-layer">
      <button aria-label="Close trace details" className="trace-drawer-backdrop" onClick={onClose} />
      <aside aria-label={`${stepNodeTitle(step)} details`} className="trace-detail-drawer" role="dialog">
        <div className="trace-drawer-header">
          <div>
            <span className="eyebrow">{stepNodeKind(step)} · sequence {step.sequence}</span>
            <h2>{stepNodeTitle(step)}</h2>
          </div>
          <IconButton label="Close trace details" onClick={onClose}>
            <X size={17} />
          </IconButton>
        </div>
        <div className="trace-drawer-scroll stack">
          <div className="row wrap">
            <StatusPill status={step.status} />
            {step.activity ? <span className="activity-label">{labelize(step.activity)}</span> : null}
            <span className="muted">{formatStepDuration(step)}</span>
          </div>
          <dl className="compact-definitions">
            <dt>Context pack</dt>
            <dd>{shortId(step.context_pack_id)}</dd>
            <dt>Iteration</dt>
            <dd>{step.iteration ?? "-"}</dd>
            <dt>Parent</dt>
            <dd>{shortId(step.parent_step_id)}</dd>
          </dl>

          <PanelSubhead title="Input summary" />
          <JsonBlock payload={step.input_summary} />
          <PanelSubhead title="Output summary" />
          <JsonBlock payload={step.output_summary} />
          {step.warning_summary.length ? (
            <>
              <PanelSubhead title="Warnings" />
              <JsonBlock payload={step.warning_summary} />
            </>
          ) : null}

          <PanelSubhead title={`Artifacts (${artifacts.length})`} />
          <div className="artifact-list drawer-artifact-list">
            {artifacts.map((artifact) => (
              <button
                className={`artifact-item ${selectedArtifact?.id === artifact.id ? "active" : ""}`}
                key={artifact.id}
                onClick={() => onSelectArtifact(artifact.id)}
                type="button"
              >
                {artifactIcon(artifact)}
                <span>{artifactLabel(artifact)}</span>
                <code>{shortSha(artifact.content_hash)}</code>
              </button>
            ))}
            {artifacts.length === 0 ? <div className="empty-state">No artifacts for this step</div> : null}
          </div>

          {selectedArtifact ? (
            <div className="artifact-detail stack">
              <dl className="compact-definitions">
                <dt>Artifact</dt>
                <dd>{artifactNodeTitle(selectedArtifact)}</dd>
                <dt>URI</dt>
                <dd className="break-anywhere">{selectedArtifact.artifact_uri}</dd>
                <dt>Hash</dt>
                <dd className="break-anywhere">{selectedArtifact.content_hash ?? "-"}</dd>
              </dl>
              <PanelSubhead title="Provenance" />
              <AsyncBoundary state={provenance}>
                <div className="stack">
                  {(provenance.data ?? []).map((ref: ProvenanceRef) => (
                    <div className="provenance-row" key={ref.id}>
                      <Link2 size={15} />
                      <div>
                        <strong>{labelize(ref.ref_type)}</strong>
                        <span className="muted break-anywhere">{ref.ref_uri}</span>
                      </div>
                    </div>
                  ))}
                  {provenance.data?.length === 0 ? (
                    <div className="empty-state">No provenance refs</div>
                  ) : null}
                </div>
              </AsyncBoundary>
            </div>
          ) : null}
        </div>
      </aside>
    </div>
  );
}

function useTraceData(agentRunId: string | null) {
  const [state, setState] = useState<LiveTraceState>(() => emptyLiveTraceState());
  const lastEventIdRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let eventSource: EventSource | null = null;
    let replaying = false;
    lastEventIdRef.current = 0;
    setState(emptyLiveTraceState(agentRunId ? "connecting" : "idle"));

    function applyExecutionEvent(event: ExecutionEvent) {
      if (cancelled || event.id <= lastEventIdRef.current) {
        return;
      }
      lastEventIdRef.current = event.id;
      const runUpdate = eventPayloadRecord<AgentRun>(event, "run");
      const stepUpdate = eventPayloadRecord<AgentStep>(event, "step");
      const artifactUpdate = eventPayloadRecord<AgentArtifact>(event, "artifact");

      setState((current) => {
        const run = runUpdate ?? current.run;
        const steps = stepUpdate ? upsertById(current.steps, stepUpdate) : current.steps;
        const artifactsByStepId = artifactUpdate
          ? upsertArtifact(current.artifactsByStepId, artifactUpdate)
          : current.artifactsByStepId;
        return {
          ...current,
          run,
          steps: [...steps].sort((left, right) => left.sequence - right.sequence),
          artifactsByStepId,
          lastEventId: event.id,
          connection: run && isTerminalAgentRun(run.status) ? "complete" : current.connection
        };
      });

      if (runUpdate && isTerminalAgentRun(runUpdate.status)) {
        eventSource?.close();
      }
    }

    async function replayDurableEvents() {
      if (!agentRunId || replaying || cancelled) {
        return;
      }
      replaying = true;
      try {
        const events = await api.listAgentRunEvents(agentRunId, lastEventIdRef.current);
        events.forEach(applyExecutionEvent);
      } catch {
        // EventSource continues its own retry cycle; a later replay will retry the durable gap.
      } finally {
        replaying = false;
      }
    }

    function connect(afterEventId: number) {
      if (!agentRunId || cancelled) {
        return;
      }
      eventSource = new EventSource(api.agentRunEventStreamUrl(agentRunId, afterEventId));
      const onEvent = (message: Event) => {
        try {
          applyExecutionEvent(JSON.parse((message as MessageEvent<string>).data) as ExecutionEvent);
        } catch {
          setState((current) => ({ ...current, error: "A live trace event could not be read." }));
        }
      };
      TRACE_EVENT_TYPES.forEach((eventType) => eventSource?.addEventListener(eventType, onEvent));
      eventSource.onopen = () => {
        if (!cancelled) {
          setState((current) => ({ ...current, connection: "live", error: null }));
        }
      };
      eventSource.onerror = () => {
        if (!cancelled) {
          setState((current) => ({ ...current, connection: "reconnecting" }));
          void replayDurableEvents();
        }
      };
    }

    async function initialize() {
      if (!agentRunId) {
        return;
      }
      try {
        const snapshot = await api.getAgentRunSnapshot(agentRunId);
        if (cancelled) {
          return;
        }
        lastEventIdRef.current = snapshot.last_event_id;
        const terminal = isTerminalAgentRun(snapshot.run.status);
        setState({
          run: snapshot.run,
          steps: [...snapshot.steps].sort((left, right) => left.sequence - right.sequence),
          artifactsByStepId: groupArtifactsByStep(snapshot.artifacts),
          lastEventId: snapshot.last_event_id,
          loading: false,
          error: null,
          connection: terminal ? "complete" : "connecting"
        });
        if (!terminal) {
          connect(snapshot.last_event_id);
        }
      } catch (exc) {
        if (!cancelled) {
          setState({
            ...emptyLiveTraceState("idle"),
            loading: false,
            error: errorMessage(exc)
          });
        }
      }
    }

    void initialize();
    return () => {
      cancelled = true;
      eventSource?.close();
    };
  }, [agentRunId]);

  return state;
}

function useHashRoute(): Route {
  const [hash, setHash] = useState(window.location.hash);

  useEffect(() => {
    const onHashChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return useMemo(() => parseRoute(hash), [hash]);
}

function parseRoute(hash: string): Route {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const [path = "/", queryString = ""] = raw.split("?");
  const query = new URLSearchParams(queryString);
  const segments = path.split("/").filter(Boolean);

  if (segments[0] === "repositories" && segments[1] && segments[2] === "settings") {
    return { name: "repositorySettings", repositoryId: segments[1], query };
  }
  if (segments[0] === "repositories" && segments[1]) {
    return { name: "repository", repositoryId: segments[1], query };
  }
  if (segments[0] === "jobs" && segments[1]) {
    return { name: "job", jobId: segments[1], query };
  }
  if (segments[0] === "generated-tests" && segments[1]) {
    return { name: "generatedTest", generatedTestId: segments[1], query };
  }
  if (segments[0] === "agent-runs" && segments[1]) {
    return { name: "agentRun", agentRunId: segments[1], query };
  }
  return { name: "repositories", query };
}

function useAsyncData<T>(loader: () => Promise<T>, deps: DependencyList) {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    loading: true,
    error: null
  });

  const reload = useCallback(async () => {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const data = await loader();
      setState({ data, loading: false, error: null });
    } catch (exc) {
      setState({ data: null, loading: false, error: errorMessage(exc) });
    }
  }, deps);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { ...state, reload };
}

function AsyncBoundary<T>({
  state,
  children
}: {
  state: AsyncState<T>;
  children: ReactNode;
}) {
  if (state.loading && state.data === null) {
    return <div className="loading-row"><CircleDashed className="spin" size={16} /> Loading</div>;
  }
  if (state.error) {
    return <InlineError message={state.error} />;
  }
  return <>{children}</>;
}

function PageTitle({
  icon,
  title,
  eyebrow,
  actions
}: {
  icon: ReactNode;
  title: string;
  eyebrow?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-title">
      <div className="title-icon">{icon}</div>
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  );
}

function PanelHeader({ title, icon }: { title: string; icon: ReactNode }) {
  return (
    <div className="panel-header">
      <div className="panel-title">
        {icon}
        <h2>{title}</h2>
      </div>
    </div>
  );
}

function PanelSubhead({ title }: { title: string }) {
  return <h3 className="panel-subhead">{title}</h3>;
}

function IconButton({
  label,
  children,
  onClick,
  disabled = false
}: {
  label: string;
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      aria-label={label}
      className="icon-button"
      disabled={disabled}
      onClick={onClick}
      title={label}
      type="button"
    >
      {children}
    </button>
  );
}

function StatusPill({ status }: { status: string }) {
  return <span className={`status-pill ${statusTone(status)}`}>{labelize(status)}</span>;
}

function Metric({
  label,
  value,
  tone
}: {
  label: string;
  value: ReactNode;
  tone?: "ok" | "warn" | "bad" | "neutral";
}) {
  return (
    <div className={`metric ${tone ?? "neutral"}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Signal({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="signal">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function InlineError({ message }: { message: string }) {
  return (
    <div className="inline-error">
      <AlertCircle size={15} />
      <span>{message}</span>
    </div>
  );
}

function FailurePanel({ title, message }: { title: string; message: string }) {
  return (
    <section className="panel failure-panel">
      <PanelHeader title={title} icon={<AlertCircle size={17} />} />
      <InlineError message={message} />
    </section>
  );
}

function JsonBlock({ payload }: { payload: unknown }) {
  return <pre className="json-block">{JSON.stringify(payload, null, 2)}</pre>;
}

function JsonPreview({ payload }: { payload: unknown }) {
  return <pre className="json-preview">{JSON.stringify(payload, null, 2)}</pre>;
}

function navigate(path: string) {
  window.location.hash = path;
}

function labelize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function shortSha(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return value.slice(0, 10);
}

function shortId(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return value.slice(0, 8);
}

function formatDate(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

function formatDuration(start: string | null | undefined, end: string | null | undefined) {
  if (!start || !end) {
    return "-";
  }
  const durationMs = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(durationMs) || durationMs < 0) {
    return "-";
  }
  if (durationMs < 1000) {
    return `${durationMs} ms`;
  }
  return `${(durationMs / 1000).toFixed(1)} s`;
}

function formatLiveDuration(start: string | null | undefined, end: string | null | undefined) {
  if (start && !end) {
    return "In progress";
  }
  return formatDuration(start, end);
}

function formatStepDuration(step: AgentStep) {
  const recordedDuration = step.output_summary.duration_ms;
  if (typeof recordedDuration === "number") {
    return recordedDuration < 1000
      ? `${recordedDuration} ms`
      : `${(recordedDuration / 1000).toFixed(1)} s`;
  }
  return formatLiveDuration(step.started_at, step.finished_at);
}

function statusTone(status: string | null | undefined): "ok" | "warn" | "bad" | "neutral" {
  if (!status) {
    return "neutral";
  }
  if (["indexed", "succeeded", "passing", "completed", "active", "low"].includes(status)) {
    return "ok";
  }
  if (["failed", "cancelled", "getting_out_of_hand", "high"].includes(status)) {
    return "bad";
  }
  if (["pending", "indexing", "running", "retry_wait", "needs_attention", "medium"].includes(status)) {
    return "warn";
  }
  return "neutral";
}

function artifactLabel(artifact: AgentArtifact) {
  if (artifact.artifact_type === "prompt") {
    return "LLM prompt";
  }
  if (artifact.artifact_type === "raw_model_response") {
    return "LLM response";
  }
  if (artifact.artifact_type === "validated_output") {
    return "Validated output";
  }
  if (artifact.artifact_type === "context_pack") {
    return "Context pack";
  }
  if (artifact.artifact_type === "trace") {
    return "Trace artifact";
  }
  return labelize(artifact.artifact_type);
}

function artifactNodeTitle(artifact: AgentArtifact) {
  const fileName = artifact.artifact_uri.split("/").filter(Boolean).at(-1);
  return fileName ?? shortId(artifact.id);
}

function artifactIcon(artifact: AgentArtifact) {
  if (artifact.artifact_type === "prompt") {
    return <FileText size={16} />;
  }
  if (artifact.artifact_type === "raw_model_response") {
    return <Send size={16} />;
  }
  if (artifact.artifact_type === "validated_output") {
    return <CheckCircle2 size={16} />;
  }
  if (artifact.artifact_type === "trace") {
    return <XCircle size={16} />;
  }
  if (artifact.artifact_type === "context_pack") {
    return <Boxes size={16} />;
  }
  return <Package size={16} />;
}

function stepNodeKind(step: AgentStep) {
  if (step.step_type === "build_test_plan") {
    return "Agent planner";
  }
  if (step.step_type === "model_call") {
    return "Model call";
  }
  if (step.step_type === "tool_call") {
    return "Repository tool";
  }
  if (step.step_type === "generate_questions") {
    return "Generation run";
  }
  if (step.step_type === "validate_output") {
    return "Contract check";
  }
  if (step.step_type === "verify_evidence") {
    return "Evidence check";
  }
  if (step.step_type === "persist_result") {
    return "Persistence";
  }
  return "Step";
}

function stepNodeTitle(step: AgentStep) {
  if (step.step_type === "build_test_plan") {
    return "Repository inspection";
  }
  if (step.step_type === "tool_call" && typeof step.input_summary.tool_name === "string") {
    return step.input_summary.tool_name;
  }
  if (step.step_type === "model_call" && typeof step.input_summary.action === "string") {
    const action = step.input_summary.action;
    if (action === "inspect_repository") {
      return "Inspect repository";
    }
    if (action === "regenerate_evidence") {
      return "Regenerate evidence";
    }
    return labelize(action);
  }
  if (step.step_type === "generate_questions") {
    return "Generate test";
  }
  if (step.step_type === "validate_output") {
    return "Validate generated test";
  }
  if (step.step_type === "verify_evidence") {
    return "Verify evidence references";
  }
  if (step.step_type === "persist_result") {
    return "Persist result";
  }
  return labelize(step.step_type);
}

function stepNodeDetail(step: AgentStep) {
  const error = step.output_summary.error;
  if (typeof error === "string" && error) {
    return error;
  }
  if (step.step_type === "build_test_plan") {
    const turns = step.output_summary.model_turn_count;
    const toolCalls = step.output_summary.completed_tool_call_count;
    if (typeof turns === "number" && typeof toolCalls === "number") {
      return `${turns} model ${turns === 1 ? "turn" : "turns"} · ${toolCalls} tool ${toolCalls === 1 ? "call" : "calls"}`;
    }
  }
  if (step.step_type === "tool_call") {
    const duration = step.output_summary.duration_ms;
    const evidence = step.output_summary.evidence_ref_count;
    if (typeof duration === "number" && typeof evidence === "number") {
      return `${duration} ms · ${evidence} evidence ${evidence === 1 ? "ref" : "refs"}`;
    }
    const argumentsValue = step.input_summary.arguments;
    if (argumentsValue && typeof argumentsValue === "object") {
      const argumentSummary = Object.entries(argumentsValue)
        .filter(([, value]) => ["string", "number", "boolean"].includes(typeof value))
        .slice(0, 2)
        .map(([key, value]) => `${key}=${String(value)}`)
        .join(" · ");
      if (argumentSummary) {
        return argumentSummary;
      }
    }
  }
  if (step.step_type === "model_call") {
    const finishReason = step.output_summary.finish_reason;
    const toolCalls = step.output_summary.tool_call_count;
    if (typeof finishReason === "string" && typeof toolCalls === "number") {
      return `${labelize(finishReason)} · ${toolCalls} requested tool ${toolCalls === 1 ? "call" : "calls"}`;
    }
  }
  if (step.step_type === "validate_output" && step.output_summary.schema_valid === true) {
    return `Valid ${String(step.output_summary.payload_type ?? "structured")} payload`;
  }
  if (step.step_type === "verify_evidence") {
    const acceptedRefs = step.output_summary.accepted_refs;
    if (Array.isArray(acceptedRefs)) {
      const validCount = acceptedRefs.length;
      return `${validCount} verified evidence ${validCount === 1 ? "reference" : "references"}`;
    }
  }
  return null;
}

function stepStateSummary(step: AgentStep) {
  if (step.activity) {
    return labelize(step.activity);
  }
  if (step.warning_summary.length) {
    return `${step.warning_summary.length} ${step.warning_summary.length === 1 ? "warning" : "warnings"}`;
  }
  return labelize(step.status);
}

function stepNodeIcon(step: AgentStep) {
  if (step.step_type === "model_call") {
    return <Activity size={17} />;
  }
  if (step.step_type === "tool_call") {
    return <Database size={17} />;
  }
  if (step.step_type === "validate_output") {
    return <CheckCircle2 size={17} />;
  }
  if (step.step_type === "verify_evidence") {
    return <Eye size={17} />;
  }
  if (step.step_type === "persist_result") {
    return <Package size={17} />;
  }
  if (step.step_type === "build_test_plan") {
    return <GitBranch size={17} />;
  }
  return <CircleDashed size={17} />;
}

function TraceConnectionIndicator({ connection }: { connection: TraceConnection }) {
  const label =
    connection === "live"
      ? "Live"
      : connection === "reconnecting"
        ? "Reconnecting"
        : connection === "complete"
          ? "Recorded"
          : connection === "connecting"
            ? "Connecting"
            : "Offline";
  return (
    <span className={`trace-connection ${connection}`}>
      <span className="node-dot" />
      {label}
    </span>
  );
}

function groupStepsByContextPack(steps: AgentStep[]) {
  const groups = new Map<string, AgentStep[]>();
  steps.forEach((step) => {
    if (!step.context_pack_id) {
      return;
    }
    groups.set(step.context_pack_id, [...(groups.get(step.context_pack_id) ?? []), step]);
  });
  return [...groups.entries()].map(([contextPackId, contextSteps]) => ({
    contextPackId,
    steps: contextSteps
  }));
}

function contextGroupTitle(steps: AgentStep[]) {
  const packType = steps
    .map((step) => step.input_summary.pack_type)
    .find((value): value is string => typeof value === "string");
  return packType ? labelize(packType) : "Generated test branch";
}

function groupStatus(steps: AgentStep[]) {
  if (steps.some((step) => step.status === "failed")) {
    return "failed";
  }
  if (steps.some((step) => step.status === "running")) {
    return "running";
  }
  if (steps.some((step) => step.status === "cancelled" || step.status === "interrupted")) {
    return "interrupted";
  }
  if (steps.length && steps.every((step) => step.status === "succeeded")) {
    return "succeeded";
  }
  return "queued";
}

function findExternalParent(groupSteps: AgentStep[], allSteps: AgentStep[]) {
  const groupStepIds = new Set(groupSteps.map((step) => step.id));
  const parentId = groupSteps
    .map((step) => step.parent_step_id)
    .find((value): value is string => Boolean(value && !groupStepIds.has(value)));
  return parentId ? allSteps.find((step) => step.id === parentId) ?? null : null;
}

function emptyLiveTraceState(connection: TraceConnection = "idle"): LiveTraceState {
  return {
    run: null,
    steps: [],
    artifactsByStepId: {},
    lastEventId: 0,
    loading: connection !== "idle",
    error: null,
    connection
  };
}

function isTerminalAgentRun(status: string) {
  return TERMINAL_AGENT_STATUSES.has(status);
}

function groupArtifactsByStep(artifacts: AgentArtifact[]) {
  return artifacts.reduce<ArtifactsByStepId>((groups, artifact) => {
    groups[artifact.agent_step_id] = [...(groups[artifact.agent_step_id] ?? []), artifact];
    return groups;
  }, {});
}

function upsertArtifact(groups: ArtifactsByStepId, artifact: AgentArtifact) {
  return {
    ...groups,
    [artifact.agent_step_id]: upsertById(groups[artifact.agent_step_id] ?? [], artifact)
  };
}

function upsertById<T extends { id: string }>(values: T[], nextValue: T) {
  const existingIndex = values.findIndex((value) => value.id === nextValue.id);
  if (existingIndex === -1) {
    return [...values, nextValue];
  }
  return values.map((value, index) => (index === existingIndex ? nextValue : value));
}

function eventPayloadRecord<T>(event: ExecutionEvent, key: string): T | null {
  const value = event.payload[key];
  return value && typeof value === "object" ? (value as T) : null;
}

function groupGeneratedTests(tests: GeneratedTest[]) {
  const grouped = tests.reduce<Record<string, GeneratedTest[]>>((groups, test) => {
    const groupId = test.agent_run_id ?? `generated-test:${test.id}`;
    groups[groupId] = [...(groups[groupId] ?? []), test];
    return groups;
  }, {});

  return Object.entries(grouped)
    .map(([id, groupedTests]) => ({
      id,
      tests: [...groupedTests].sort(
        (left, right) =>
          left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id)
      )
    }))
    .sort((left, right) => {
      const leftCreatedAt = left.tests.at(-1)?.created_at ?? "";
      const rightCreatedAt = right.tests.at(-1)?.created_at ?? "";
      return rightCreatedAt.localeCompare(leftCreatedAt);
    });
}

function draftFromSubmittedAnswer(answer: TestAnswer | null): AnswerDraft | null {
  if (!answer) {
    return null;
  }
  if (answer.answer_payload.type === "short_answer") {
    return {
      ...EMPTY_ANSWER_DRAFT,
      responseText: answer.answer_payload.response_text
    };
  }
  if (answer.answer_payload.type === "mcq_single") {
    return {
      ...EMPTY_ANSWER_DRAFT,
      selectedOptionId: answer.answer_payload.selected_option_id
    };
  }
  return {
    ...EMPTY_ANSWER_DRAFT,
    selectedOptionIds: answer.answer_payload.selected_option_ids
  };
}

function answerPayloadFromDraft(
  test: GeneratedTest,
  draft: AnswerDraft
): TestAnswerPayload | null {
  if (test.presentation_payload.type === "short_answer") {
    const responseText = draft.responseText.trim();
    return responseText ? { type: "short_answer", response_text: responseText } : null;
  }
  if (test.presentation_payload.type === "mcq_single") {
    return draft.selectedOptionId
      ? { type: "mcq_single", selected_option_id: draft.selectedOptionId }
      : null;
  }
  return draft.selectedOptionIds.length
    ? { type: "mcq_multi", selected_option_ids: draft.selectedOptionIds }
    : null;
}

function testSessionState(
  progress: TestSessionProgress[string] | undefined,
  draft: AnswerDraft | undefined
) {
  if (progress?.results.length) {
    return "judged";
  }
  if (progress?.answers.length) {
    return "completed";
  }
  if (
    draft &&
    (draft.responseText.trim() || draft.selectedOptionId || draft.selectedOptionIds.length)
  ) {
    return "in_progress";
  }
  return "unanswered";
}

function testTitle(test: GeneratedTest) {
  return test.presentation_payload.question || `${labelize(test.category)} test`;
}

function formatScore(value: string) {
  const score = Number.parseFloat(value);
  return Number.isFinite(score) ? `${Math.round(score * 100)}%` : value;
}

function errorMessage(exc: unknown) {
  return exc instanceof Error ? exc.message : "Request failed";
}
