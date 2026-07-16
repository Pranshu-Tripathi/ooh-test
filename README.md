# ooh-test

Out Of Hands Test is a local-first developer tool for keeping humans in command of fast-moving codebases.

Phase 1 starts with a Docker Compose MVP:

- FastAPI API service
- background worker service
- Postgres metadata store
- one-shot migration service
- durable repo-local cache directory
- native host Ollama access through `host.docker.internal`

## Local Runtime

Start Compose with the built-in local defaults:

```bash
docker compose up --build
```

Optionally copy `.env.example` to `.env` if you want to override ports, model names, or
the read-only repository mount used by the worker.

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

Enqueue another ingest after new commits land:

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

Build deterministic context packs from the latest snapshot:

```bash
curl -X POST http://localhost:8500/repositories/{repository_id}/context-packs
```

List context packs:

```bash
curl http://localhost:8500/repositories/{repository_id}/context-packs
```

Generate repository-specific tests from the latest snapshot. During smoke testing, request a
single pack type so the local model does one focused generation first:

```bash
curl -X POST http://localhost:8500/repositories/{repository_id}/generate-test-jobs \
  -H "content-type: application/json" \
  -d '{"pack_types":["low_level_components"]}'
```

After the job succeeds, list generated tests:

```bash
curl http://localhost:8500/repositories/{repository_id}/generated-tests
```

Submit an answer and enqueue judging:

```bash
curl -X POST http://localhost:8500/generated-tests/{generated_test_id}/answers \
  -H "content-type: application/json" \
  -d '{"answer_text":"The component reads the repository snapshot and builds bounded context."}'
```

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

Repository registration stores metadata and enqueues an `ingest_repository` job. The worker
expects local `source_uri` values to be paths visible inside the container. By default, Compose
mounts the current project at `/workspace`; set
`OOH_REPOSITORY_MOUNT=/host/path:/workspace:ro` in `.env` to inspect a different local
repository. GitHub repositories are cloned or fast-forwarded under `OOH_CACHE_ROOT`, which
Compose mounts to `.ooh_cache/` in this project.

The worker creates a deterministic metadata snapshot in `OOH_CACHE_ROOT`, records a
`repo_snapshots` row, discovers guidance files such as `AGENTS.md`, `README.md`, `.cursor/**`,
`docs/**`, and `adr/**`, records baseline and commit-to-commit drift events using the active
attention profile if one exists, and marks the repository indexed at the resolved commit SHA.
Generation jobs build bounded context-pack artifacts with prompt-safe code and guidance excerpts,
call the configured local model through Ollama, validate schema and evidence refs, and persist
generated tests plus agent traces. Judging jobs score submitted answers, persist result history,
and save model-suggested learnings when returned.

Local model calls default to a 300 second timeout. Override with
`OOH_MODEL_TIMEOUT_SECONDS` in `.env` when running slower models or larger repositories.

Phase 1 is being implemented in reviewable components.

## Migrations

The Compose `migrate` service runs Alembic before the API and worker start:

```bash
docker compose run --rm migrate
```

The API and worker readiness checks require the database to be at the packaged Alembic head revision.
