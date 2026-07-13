# ooh-test

Out Of Hands Test is a local-first developer tool for keeping humans in command of fast-moving codebases.

Phase 1 starts with a Docker Compose MVP:

- FastAPI API service
- background worker service
- Postgres metadata store
- one-shot migration service
- durable local cache volume
- native host Ollama access through `host.docker.internal`

## Local Runtime

Start Compose with the built-in local defaults:

```bash
docker compose up --build
```

Optionally copy `.env.example` to `.env` if you want to override ports, model names, or local paths.

Useful endpoints once the API is running:

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
```

Register a local repository:

```bash
curl -X POST http://localhost:8080/repositories \
  -H "content-type: application/json" \
  -d '{"source_type":"local_path","source_uri":"/path/to/repo"}'
```

Repository registration stores metadata and enqueues an `ingest_repository` job. The worker will process that job in a later Phase 1 slice.

Phase 1 is being implemented in reviewable components. The current component only establishes the application scaffold and Compose runtime.

## Migrations

The Compose `migrate` service runs Alembic before the API and worker start:

```bash
docker compose run --rm migrate
```

The API and worker readiness checks require the database to be at the packaged Alembic head revision.
