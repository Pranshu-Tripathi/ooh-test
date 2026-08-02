# ooh-test

Out Of Hands Test is a local-first developer tool for keeping humans in command of fast-moving codebases.

The Phase 2 development runtime uses Docker Compose with:

- FastAPI API service
- background worker service
- drift-trigger scheduler service
- Postgres metadata store
- one-shot migration service
- durable repo-local cache directory
- native host Ollama access through `host.docker.internal`

Phase 3 is adding Docker Desktop Kubernetes as an optional advanced runtime. Its resources are
restricted to the `docker-desktop` context and `ooh-test` namespace, and local access uses
port-forwarding on host ports `8500`–`8510`. See [k8s/README.md](k8s/README.md) for the current
checkpoint and guarded command workflow. Compose remains the working fallback while the
Kubernetes rollout proceeds.

## Local Runtime

Start Compose with the built-in local defaults:

```bash
docker compose up --build
```

Optionally copy `.env.example` to `.env` if you want to override ports, model names, or
the read-only repository mount used by the scheduler and worker.

Test generation defaults to the available `qwen3:8b` Ollama model. The generation prompt is
limited to 8,000 UTF-8 bytes, including instructions, repair feedback, and repository context;
model-directed repo-tool observations are limited to 2,500 bytes inside that total. Override these
with `OOH_GENERATION_PROMPT_MAX_BYTES` and `OOH_TOOL_OBSERVATION_MAX_BYTES` after increasing the
model context window and confirming memory headroom.

Default host-facing ports live in the `8500-8510` range to avoid common development ports:
API/UI on `8500`, Postgres on `8501`, and the Vite dev server on `8502`.

Useful endpoints once the API is running:

```bash
curl http://localhost:8500/healthz
curl http://localhost:8500/readyz
```

Open the small dashboard at http://localhost:8500/ to register a repository, watch jobs,
generate a test, submit an answer, and inspect results.

Register a local repository:

```bash
curl -X POST http://localhost:8500/repositories \
  -H "content-type: application/json" \
  -d '{"source_type":"local_path","source_uri":"/workspace"}'
```

Register a public GitHub repository:

```bash
curl -X POST http://localhost:8500/repositories \
  -H "content-type: application/json" \
  -d '{"source_type":"github","source_uri":"https://github.com/owner/repo.git"}'
```

The scheduler detects new commits and enqueues ingestion automatically. The endpoint remains
available for an explicit re-ingest:

```bash
curl -X POST http://localhost:8500/repositories/{repository_id}/ingest-jobs
```

Poll any background job:

```bash
curl http://localhost:8500/jobs/{job_id}
```

List drift events for a repository:

```bash
curl http://localhost:8500/repositories/{repository_id}/drift-events
```

Configure an inclusive drift-score range that generates low-level tests for new commit-to-commit
drift events. Omit `drift_max_score` for an open-ended range:

```bash
curl -X PUT http://localhost:8500/repositories/{repository_id}/schedule \
  -H "content-type: application/json" \
  -d '{
    "enabled": true,
    "drift_min_score": "25",
    "drift_max_score": "80",
    "pack_types": ["low_level_components"]
  }'

curl http://localhost:8500/repositories/{repository_id}/schedule
```

The scheduler polls local `HEAD` or the configured remote branch at
`OOH_REPOSITORY_POLL_INTERVAL_SECONDS`, skips repositories with an active ingestion job, and
deduplicates ingestion by repository and commit range. Multiple commits since the last successful
ingestion are handled as one aggregate range. Ingestion checks out the exact detected commit,
creates the snapshot, calculates and persists drift, and only then advances the repository's
processed commit. Uncommitted local files are not ingested.

The scheduler then records every drift evaluation, ignores baseline drift, and uses an idempotency
key derived from the schedule and drift event. Enabling a schedule starts from that moment and does
not replay older drift events. Scheduled generation remains bound to the snapshot and drift event
that caused the trigger.

Build deterministic context packs from the latest snapshot:

```bash
curl -X POST http://localhost:8500/repositories/{repository_id}/context-packs
```

List context packs:

```bash
curl http://localhost:8500/repositories/{repository_id}/context-packs
```

Generate repository-specific tests from the latest snapshot. A generation plan can request 1–5
questions per category, up to 15 questions in one job. Each planned question uses an independent
model call and validation/evidence loop:

```bash
curl -X POST http://localhost:8500/repositories/{repository_id}/generate-test-jobs \
  -H "content-type: application/json" \
  -d '{
    "generation_plan": [
      {"category": "low_level_components", "question_count": 3},
      {"category": "design_decisions", "question_count": 2}
    ]
  }'
```

Generation is sequential to keep local-model load bounded. A job succeeds when at least one planned
question is valid; its result metadata records requested, generated, and failed counts plus
per-question failure details. Legacy `pack_types` requests remain supported and use the configured
default for each selected category.

Set `OOH_GENERATION_QUESTIONS_PER_CATEGORY` to control the default question count used by manual
generation, legacy requests, and drift-triggered scheduler jobs. The API exposes this runtime value
to the frontend, so all processes use the same default. Explicit `generation_plan` counts still
override it. Its accepted range is 1–3 so the default remains safe when all five categories are
selected. Explicit plans retain per-category and per-job safety limits of 5 and 15 respectively.

After the job succeeds, list generated tests:

```bash
curl http://localhost:8500/repositories/{repository_id}/generated-tests
```

Submit an answer and enqueue judging:

```bash
curl -X POST http://localhost:8500/generated-tests/{generated_test_id}/answers \
  -H "content-type: application/json" \
  -d '{"type":"short_answer","response_text":"The component reads the repository snapshot and builds bounded context."}'
```

Single-choice answers use `{"type":"mcq_single","selected_option_id":"A"}` and multi-choice
answers use `{"type":"mcq_multi","selected_option_ids":["A","C"]}`. Generated-test responses expose
only the public `presentation_payload`; grading guidance is returned with a judged result.

After the judge job succeeds, inspect the scored result and any saved learnings:

```bash
curl http://localhost:8500/generated-tests/{generated_test_id}/results
curl http://localhost:8500/repositories/{repository_id}/test-results
curl http://localhost:8500/repositories/{repository_id}/learnings
```

Create an active attention profile to weight drift by path:

```bash
curl -X POST http://localhost:8500/repositories/{repository_id}/attention-profiles \
  -H "content-type: application/json" \
  -d '{
    "name": "Backend heavy",
    "default_weight": "1.0",
    "focus_areas": [
      {"name": "API", "weight": "3.0", "path_globs": ["src/api/**", "src/ooh/api/**"]}
    ]
  }'
```

Repository registration stores metadata and enqueues an `ingest_repository` job. The scheduler and
worker expect local `source_uri` values to be paths visible inside their containers. By default,
Compose mounts the current project at `/workspace`; set
`OOH_REPOSITORY_MOUNT=/host/path:/workspace:ro` in `.env` to inspect a different local
repository. The worker creates a detached, committed-tree checkout under `OOH_CACHE_ROOT` for both
local and GitHub sources, which Compose mounts to `.ooh_cache/` in this project.

The worker creates a deterministic metadata snapshot in `OOH_CACHE_ROOT`, records a
`repo_snapshots` row, discovers guidance files such as `AGENTS.md`, `README.md`, `.cursor/**`,
`docs/**`, and `adr/**`, records baseline and commit-to-commit drift events using the active
attention profile if one exists, and marks the repository indexed at the resolved commit SHA.
Generation jobs build bounded context-pack artifacts with prompt-safe code and guidance excerpts.
For indexed snapshots, the model can inspect the repository through native Ollama tool calls backed
by the immutable tree-sitter index. Novel calls are not count-limited; identical calls are rejected,
tool path and byte boundaries remain application-controlled, and every executed call is traced. The
same ten-minute deadline covers inspection and final generation for a context pack. Final generation
uses the selected test type's lean wire schema, validates the result against the stricter canonical
Pydantic contract, enriches evidence refs, and persists the generated test plus its agent trace.
Schema enforcement failures fail the model call instead of falling back to unconstrained JSON.
Judging jobs score submitted answers, persist result history, and save model-suggested learnings when
returned.

Local model calls default to a 300 second timeout. Override with
`OOH_MODEL_TIMEOUT_SECONDS` in `.env` when running slower models or larger repositories.
The shared inspection and generation deadline defaults to 600 seconds and can be changed with
`OOH_AGENT_LOOP_TIMEOUT_SECONDS`.

Workers claim jobs with durable leases. `OOH_WORKER_LEASE_SECONDS` must exceed the maximum bounded
duration of a job; expired claims are recovered automatically and stale workers cannot finalize a
reclaimed attempt. Set `OOH_WORKER_JOB_TYPES` to a comma-separated list such as
`ingest_repository,compute_drift` when running role-specific worker replicas. Logs default to JSON
for container collection; set `OOH_LOG_FORMAT=text` for local human-readable output.

The Compose scheduler logs an INFO heartbeat after every repository poll, including checked,
unchanged, changed, queued, active, and failed counts. It also evaluates durable drift events every
`OOH_SCHEDULER_POLL_INTERVAL_SECONDS`. A future Kubernetes CronJob can run both behaviors once with:

```bash
python -m ooh.scheduler.main --once
```

Phase 2 is being implemented in reviewable components.

## Migrations

The Compose `migrate` service runs Alembic before the API and worker start:

```bash
docker compose run --rm migrate
```

The API and worker readiness checks require the database to be at the packaged Alembic head revision.
