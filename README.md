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

Useful endpoints once the API is running:

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
```

Register a local repository:

```bash
curl -X POST http://localhost:8080/repositories \
  -H "content-type: application/json" \
  -d '{"source_type":"local_path","source_uri":"/workspace"}'
```

Register a public GitHub repository:

```bash
curl -X POST http://localhost:8080/repositories \
  -H "content-type: application/json" \
  -d '{"source_type":"github","source_uri":"https://github.com/owner/repo.git"}'
```

Enqueue another ingest after new commits land:

```bash
curl -X POST http://localhost:8080/repositories/{repository_id}/ingest-jobs
```

List drift events for a repository:

```bash
curl http://localhost:8080/repositories/{repository_id}/drift-events
```

Build deterministic context packs from the latest snapshot:

```bash
curl -X POST http://localhost:8080/repositories/{repository_id}/context-packs
```

List context packs:

```bash
curl http://localhost:8080/repositories/{repository_id}/context-packs
```

Create an active attention profile to weight drift by path:

```bash
curl -X POST http://localhost:8080/repositories/{repository_id}/attention-profiles \
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

The worker currently creates a deterministic metadata snapshot in `OOH_CACHE_ROOT`, records a
`repo_snapshots` row, discovers guidance files such as `AGENTS.md`, `README.md`, `.cursor/**`,
`docs/**`, and `adr/**`, records baseline and commit-to-commit drift events using the active
attention profile if one exists, and marks the repository indexed at the resolved commit SHA.
The API can then build deterministic context-pack artifacts for later test generation.

Phase 1 is being implemented in reviewable components.

## Migrations

The Compose `migrate` service runs Alembic before the API and worker start:

```bash
docker compose run --rm migrate
```

The API and worker readiness checks require the database to be at the packaged Alembic head revision.
