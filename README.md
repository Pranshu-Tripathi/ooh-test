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

Phase 1 is being implemented in reviewable components. The current component only establishes the application scaffold and Compose runtime.
