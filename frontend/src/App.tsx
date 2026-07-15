import {
  Activity,
  AlertCircle,
  Boxes,
  CheckCircle2,
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
  XCircle
} from "lucide-react";
import type { DependencyList, FormEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "./api";
import type {
  AgentArtifact,
  AgentRun,
  AgentStep,
  AttentionProfile,
  ContextPack,
  DriftEvent,
  GeneratedTest,
  Job,
  JobDetail,
  ProvenanceRef,
  Repository,
  SavedLearning,
  TestAnswer,
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

const PACK_TYPES = [
  "high_level_design",
  "low_level_components",
  "design_decisions",
  "future_improvements",
  "active_pr"
];

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
  const [actionJob, setActionJob] = useState<Job | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selectedPackTypes, setSelectedPackTypes] = useState<string[]>(["low_level_components"]);

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
          onClick={() => void runAction(() => api.enqueueGenerateTest(repositoryId, selectedPackTypes))}
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
      </div>
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

  return (
    <section className="panel">
      <PanelHeader title="Generated tests" icon={<FileText size={17} />} />
      <AsyncBoundary state={tests}>
        <div className="table-list">
          {(tests.data ?? []).map((test) => (
            <a
              className="table-row tall"
              href={`#/generated-tests/${test.id}?repositoryId=${repositoryId}`}
              key={test.id}
            >
              <div>
                <strong>{testTitle(test)}</strong>
                <span className="muted">{labelize(test.category)}</span>
              </div>
              <span>{test.evidence_refs.length} refs</span>
              <span>{resultsByTest[test.id]?.length ?? 0} results</span>
              <ChevronRight size={16} />
            </a>
          ))}
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
            loading={latestTrace.steps.loading || latestTrace.artifactsByStepId.loading}
            steps={latestTrace.steps.data ?? []}
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
  selectedStepId,
  selectedArtifactId,
  onSelectStep,
  onSelectArtifact
}: {
  steps: AgentStep[];
  artifactsByStepId: AsyncState<ArtifactsByStepId>;
  loading: boolean;
  selectedStepId?: string | null;
  selectedArtifactId?: string | null;
  onSelectStep?: (stepId: string) => void;
  onSelectArtifact?: (stepId: string, artifactId: string) => void;
}) {
  if (loading && steps.length === 0) {
    return (
      <div className="loading-row">
        <CircleDashed className="spin" size={16} />
        Loading trace graph
      </div>
    );
  }
  if (artifactsByStepId.error) {
    return <InlineError message={artifactsByStepId.error} />;
  }
  return (
    <TraceGraph
      artifactsByStepId={artifactsByStepId.data ?? {}}
      onSelectArtifact={onSelectArtifact}
      onSelectStep={onSelectStep}
      selectedArtifactId={selectedArtifactId}
      selectedStepId={selectedStepId}
      steps={steps}
    />
  );
}

function TraceGraph({
  steps,
  artifactsByStepId,
  selectedStepId,
  selectedArtifactId,
  onSelectStep,
  onSelectArtifact
}: {
  steps: AgentStep[];
  artifactsByStepId: ArtifactsByStepId;
  selectedStepId?: string | null;
  selectedArtifactId?: string | null;
  onSelectStep?: (stepId: string) => void;
  onSelectArtifact?: (stepId: string, artifactId: string) => void;
}) {
  const artifactCount = Object.values(artifactsByStepId).reduce(
    (count, artifacts) => count + artifacts.length,
    0
  );
  const modelArtifactCount = Object.values(artifactsByStepId)
    .flat()
    .filter((artifact) => isModelArtifact(artifact)).length;

  return (
    <div className="trace-graph-wrap">
      <div className="trace-legend">
        <span>{steps.length} steps</span>
        <span>{modelArtifactCount} LLM artifacts</span>
        <span>{artifactCount} artifacts</span>
      </div>
      <div className="trace-graph">
        {steps.map((step) => {
          const artifacts = artifactsByStepId[step.id] ?? [];
          return (
            <div className="trace-lane" key={step.id}>
              <button
                className={`trace-node step-node ${selectedStepId === step.id ? "active" : ""}`}
                onClick={() => onSelectStep?.(step.id)}
                type="button"
              >
                <span className={`node-dot ${statusTone(step.status)}`} />
                <span className="node-kind">Step {step.sequence}</span>
                <strong>{labelize(step.step_type)}</strong>
                <StatusPill status={step.status} />
              </button>
              <div className="artifact-chain">
                {artifacts.length === 0 ? (
                  <div className="trace-empty">No artifacts recorded</div>
                ) : (
                  artifacts.map((artifact) => (
                    <button
                      className={[
                        "trace-node",
                        "artifact-node",
                        artifactClass(artifact),
                        selectedArtifactId === artifact.id ? "active" : ""
                      ].join(" ")}
                      key={artifact.id}
                      onClick={() => onSelectArtifact?.(step.id, artifact.id)}
                      type="button"
                    >
                      {artifactIcon(artifact)}
                      <span className="node-kind">{artifactLabel(artifact)}</span>
                      <strong>{artifactNodeTitle(artifact)}</strong>
                      <code>{shortSha(artifact.content_hash)}</code>
                    </button>
                  ))
                )}
              </div>
            </div>
          );
        })}
        {steps.length === 0 ? <div className="empty-state">No steps</div> : null}
      </div>
    </div>
  );
}

function RepositorySettingsPage({ repositoryId }: { repositoryId: string }) {
  const repository = useAsyncData(() => api.getRepository(repositoryId), [repositoryId]);
  const profiles = useAsyncData(() => api.listAttentionProfiles(repositoryId), [repositoryId]);

  return (
    <div className="page stack">
      <PageTitle
        icon={<Settings size={22} />}
        title={`${repository.data?.name ?? "Repository"} settings`}
        eyebrow="Attention profiles"
      />
      <section className="panel">
        <PanelHeader title="Attention profiles" icon={<Layers size={17} />} />
        <AsyncBoundary state={profiles}>
          <div className="card-grid">
            {(profiles.data ?? []).map((profile: AttentionProfile) => (
              <article className="item-card" key={profile.id}>
                <div className="item-card-header">
                  <strong>{profile.name}</strong>
                  {profile.active ? <StatusPill status="active" /> : <StatusPill status="saved" />}
                </div>
                <dl className="compact-definitions">
                  <dt>Default weight</dt>
                  <dd>{String(profile.default_weight)}</dd>
                  <dt>Focus areas</dt>
                  <dd>{profile.focus_areas.length}</dd>
                </dl>
                <div className="source-list">
                  {profile.focus_areas.map((area) => (
                    <span className="source-pill" key={area.id}>
                      {area.name}: {area.path_globs.join(", ")}
                    </span>
                  ))}
                </div>
              </article>
            ))}
            {profiles.data?.length === 0 ? <div className="empty-state">No attention profiles</div> : null}
          </div>
        </AsyncBoundary>
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
                <dt>Error</dt>
                <dd>{job.data.error_summary ?? "-"}</dd>
              </dl>
            </section>
            <section className="panel">
              <PanelHeader title="Payload" icon={<Package size={17} />} />
              <JsonBlock payload={job.data.payload} />
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
  const answers = useAsyncData(() => api.listAnswers(generatedTestId), [generatedTestId]);
  const results = useAsyncData(() => api.listResults(generatedTestId), [generatedTestId]);
  const [answerText, setAnswerText] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [judgeJob, setJudgeJob] = useState<Job | null>(null);
  const test = tests.data?.find((item) => item.id === generatedTestId) ?? null;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError(null);
    try {
      const result = await api.submitAnswer(generatedTestId, answerText);
      setJudgeJob(result.judge_job);
      setAnswerText("");
      await answers.reload();
      await results.reload();
    } catch (exc) {
      setSubmitError(errorMessage(exc));
    }
  }

  return (
    <div className="page stack">
      <PageTitle icon={<FileText size={22} />} title="Generated test" eyebrow={shortId(generatedTestId)} />
      <div className="two-column wide-right">
        <section className="panel">
          <PanelHeader title={test ? testTitle(test) : "Test payload"} icon={<FileText size={17} />} />
          {test ? (
            <div className="stack">
              <div className="row wrap">
                <StatusPill status={labelize(test.category)} />
                {test.agent_run_id ? (
                  <a className="inline-link" href={`#/agent-runs/${test.agent_run_id}`}>
                    <Network size={14} />
                    Agent run
                  </a>
                ) : null}
              </div>
              <JsonBlock payload={test.test_payload} />
              <PanelSubhead title="Evidence" />
              <JsonBlock payload={test.evidence_refs} />
            </div>
          ) : (
            <div className="empty-state">Test payload unavailable</div>
          )}
        </section>

        <section className="panel">
          <PanelHeader title="Answer and results" icon={<Send size={17} />} />
          <form className="answer-form" onSubmit={onSubmit}>
            <textarea
              required
              value={answerText}
              onChange={(event) => setAnswerText(event.target.value)}
            />
            {submitError ? <InlineError message={submitError} /> : null}
            <button className="primary action-button" type="submit">
              <Send size={15} />
              Submit
            </button>
          </form>
          {judgeJob ? (
            <div className="notice">
              <Clock3 size={15} />
              <a href={`#/jobs/${judgeJob.id}`}>{labelize(judgeJob.job_type)}</a>
              <StatusPill status={judgeJob.status} />
            </div>
          ) : null}
          <PanelSubhead title="Results" />
          <AsyncBoundary state={results}>
            <div className="stack">
              {(results.data ?? []).map((result) => (
                <div className="result-card" key={result.id}>
                  <div className="item-card-header">
                    <strong>{String(result.score)}</strong>
                    <StatusPill status={result.status} />
                  </div>
                  <JsonPreview payload={result.feedback} />
                </div>
              ))}
              {results.data?.length === 0 ? <div className="empty-state">No results</div> : null}
            </div>
          </AsyncBoundary>
          <PanelSubhead title="Answers" />
          <AsyncBoundary state={answers}>
            <div className="stack">
              {(answers.data ?? []).map((answer: TestAnswer) => (
                <JsonPreview key={answer.id} payload={answer.answer_payload} />
              ))}
              {answers.data?.length === 0 ? <div className="empty-state">No answers</div> : null}
            </div>
          </AsyncBoundary>
        </section>
      </div>
    </div>
  );
}

function AgentRunPage({ agentRunId }: { agentRunId: string }) {
  const run = useAsyncData(() => api.getAgentRun(agentRunId), [agentRunId]);
  const traceData = useTraceData(agentRunId);
  const steps = traceData.steps;
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const selectedStep = steps.data?.find((step) => step.id === selectedStepId) ?? steps.data?.[0] ?? null;
  const artifacts = useAsyncData(
    () => (selectedStep ? api.listAgentArtifacts(selectedStep.id) : Promise.resolve([])),
    [selectedStep?.id]
  );
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const selectedArtifact =
    artifacts.data?.find((artifact) => artifact.id === selectedArtifactId) ?? artifacts.data?.[0] ?? null;
  const provenance = useAsyncData(
    () => (selectedArtifact ? api.listArtifactProvenance(selectedArtifact.id) : Promise.resolve([])),
    [selectedArtifact?.id]
  );

  useEffect(() => {
    if (!selectedStepId && steps.data?.[0]) {
      setSelectedStepId(steps.data[0].id);
    }
  }, [selectedStepId, steps.data]);

  useEffect(() => {
    if (
      selectedArtifactId &&
      artifacts.data &&
      !artifacts.data.some((artifact) => artifact.id === selectedArtifactId)
    ) {
      setSelectedArtifactId(null);
    }
  }, [artifacts.data, selectedArtifactId]);

  const allTraceArtifacts = traceData.artifactsByStepId.data ?? {};
  const modelArtifactCount = Object.values(allTraceArtifacts)
    .flat()
    .filter((artifact) => isModelArtifact(artifact)).length;

  return (
    <div className="page stack">
      <PageTitle
        icon={<Network size={22} />}
        title={run.data ? labelize(run.data.run_type) : "Agent run"}
        eyebrow={shortId(agentRunId)}
      />
      <AsyncBoundary state={run}>
        {run.data ? (
          <div className="metric-grid">
            <Metric label="Status" value={run.data.status} tone={statusTone(run.data.status)} />
            <Metric label="Model" value={run.data.model_profile ?? "-"} />
            <Metric label="Duration" value={formatDuration(run.data.started_at, run.data.finished_at)} />
            <Metric label="LLM artifacts" value={modelArtifactCount} tone="neutral" />
          </div>
        ) : null}
      </AsyncBoundary>

      <section className="panel">
        <PanelHeader title="Trace graph" icon={<Network size={17} />} />
        <TracePreview
          artifactsByStepId={traceData.artifactsByStepId}
          loading={traceData.steps.loading || traceData.artifactsByStepId.loading}
          onSelectArtifact={(stepId, artifactId) => {
            setSelectedStepId(stepId);
            setSelectedArtifactId(artifactId);
          }}
          onSelectStep={(stepId) => setSelectedStepId(stepId)}
          selectedArtifactId={selectedArtifact?.id ?? selectedArtifactId}
          selectedStepId={selectedStep?.id ?? selectedStepId}
          steps={traceData.steps.data ?? []}
        />
      </section>

      <div className="two-column wide-right">
        <section className="panel">
          <PanelHeader title="Step detail" icon={<Eye size={17} />} />
          {selectedStep ? (
            <div className="stack">
              <div className="row wrap">
                <StatusPill status={selectedStep.status} />
                <span className="muted">Sequence {selectedStep.sequence}</span>
                <span className="muted">{formatDuration(selectedStep.started_at, selectedStep.finished_at)}</span>
              </div>
              <PanelSubhead title="Input" />
              <JsonBlock payload={selectedStep.input_summary} />
              <PanelSubhead title="Output" />
              <JsonBlock payload={selectedStep.output_summary} />
              {selectedStep.warning_summary.length ? (
                <>
                  <PanelSubhead title="Warnings" />
                  <JsonBlock payload={selectedStep.warning_summary} />
                </>
              ) : null}
            </div>
          ) : (
            <div className="empty-state">No selected step</div>
          )}
        </section>

        <section className="panel">
          <PanelHeader title="Artifacts and provenance" icon={<Package size={17} />} />
          <AsyncBoundary state={artifacts}>
            <div className="artifact-list">
              {(artifacts.data ?? []).map((artifact: AgentArtifact) => (
                <button
                  className={`artifact-item ${selectedArtifact?.id === artifact.id ? "active" : ""}`}
                  key={artifact.id}
                  onClick={() => setSelectedArtifactId(artifact.id)}
                >
                  <Package size={15} />
                  <span>{artifactLabel(artifact)}</span>
                  <code>{shortSha(artifact.content_hash)}</code>
                </button>
              ))}
              {artifacts.data?.length === 0 ? <div className="empty-state">No artifacts</div> : null}
            </div>
          </AsyncBoundary>
          {selectedArtifact ? (
            <div className="stack">
              <dl className="compact-definitions">
                <dt>URI</dt>
                <dd className="break-anywhere">{selectedArtifact.artifact_uri}</dd>
                <dt>Hash</dt>
                <dd>{selectedArtifact.content_hash ?? "-"}</dd>
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
                  {provenance.data?.length === 0 ? <div className="empty-state">No provenance refs</div> : null}
                </div>
              </AsyncBoundary>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}

function useTraceData(agentRunId: string | null) {
  const steps = useAsyncData(
    () => (agentRunId ? api.listAgentSteps(agentRunId) : Promise.resolve([])),
    [agentRunId]
  );
  const stepIds = useMemo(() => (steps.data ?? []).map((step) => step.id).join("|"), [steps.data]);
  const artifactsByStepId = useAsyncData(async () => {
    if (!steps.data?.length) {
      return {};
    }
    const entries = await Promise.all(
      steps.data.map(async (step) => [step.id, await api.listAgentArtifacts(step.id)] as const)
    );
    return Object.fromEntries(entries);
  }, [stepIds]);

  return { steps, artifactsByStepId };
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
  onClick
}: {
  label: string;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button aria-label={label} className="icon-button" onClick={onClick} title={label}>
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

function artifactClass(artifact: AgentArtifact) {
  if (artifact.artifact_type === "prompt") {
    return "prompt-artifact";
  }
  if (artifact.artifact_type === "raw_model_response") {
    return "model-artifact";
  }
  if (artifact.artifact_type === "validated_output") {
    return "validated-artifact";
  }
  if (artifact.artifact_type === "trace") {
    return "trace-artifact";
  }
  return "support-artifact";
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

function isModelArtifact(artifact: AgentArtifact) {
  return artifact.artifact_type === "prompt" || artifact.artifact_type === "raw_model_response";
}

function testTitle(test: GeneratedTest) {
  const payload = test.test_payload;
  const title =
    payload.title ??
    payload.question ??
    payload.prompt ??
    payload.name ??
    `${labelize(test.category)} test`;
  return typeof title === "string" ? title : `${labelize(test.category)} test`;
}

function errorMessage(exc: unknown) {
  return exc instanceof Error ? exc.message : "Request failed";
}
