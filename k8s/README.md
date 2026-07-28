# Docker Desktop Kubernetes runtime

Phase 3 targets the Kubernetes cluster managed by Docker Desktop. The runtime is isolated from
other projects by two fixed boundaries:

- kubectl context: `docker-desktop`
- Kubernetes namespace: `ooh-test`

The repository does not change the global kubectl context or its default namespace. Use the
project wrapper for every cluster command:

```bash
./scripts/kubectl-ooh-test get pods
```

The wrapper always supplies `--context docker-desktop --namespace ooh-test` and refuses
`kubectl config` commands. This is important on machines whose active context points at another
cluster.

## Provider and isolation

Prerequisites:

- Docker Desktop is running.
- Kubernetes is enabled in Docker Desktop.
- Docker Desktop Kubernetes uses the `kind` provisioner.
- `kubectl config get-contexts docker-desktop` returns the Docker Desktop context.
- `kubectl --context docker-desktop get nodes` reports a ready node.

Create or reconcile the project namespace:

```bash
./scripts/kubectl-ooh-test apply -k k8s/base
./scripts/kubectl-ooh-test get namespace ooh-test
```

Compose remains usable throughout the rollout.

## Checkpoint 2: configuration, secrets, and storage

The base currently creates:

- `ooh-runtime`, a ConfigMap containing the non-secret Phase 2 runtime defaults;
- `ooh-database`, a Secret containing local-only Postgres bootstrap credentials and the application
  database URL;
- `postgres-data`, a 5 GiB PVC for Postgres;
- `ooh-cache`, a 10 GiB PVC shared by the API and workers in later checkpoints.

Apply and inspect the checkpoint:

```bash
./scripts/kubectl-ooh-test apply -k k8s/base
./scripts/kubectl-ooh-test get configmap,secret,pvc
```

The committed database password is intentionally a non-sensitive development credential, like the
existing Compose defaults. Never add GitHub tokens, personal access tokens, encryption keys, or
non-local database credentials to this manifest.

Compose-only host-port settings are omitted because Kubernetes access uses `kubectl port-forward`.
`OOH_WORKER_JOB_TYPES` will be set per role-specific Deployment, and `OOH_REPOSITORY_MOUNT` remains
deferred until the Docker Desktop read-only local-repository mount checkpoint.

Both PVCs use Docker Desktop's `standard` local-path StorageClass and `ReadWriteOnce`. Docker
Desktop is a single-node cluster, so the cache may be mounted by multiple project pods scheduled on
that node. The StorageClass uses `WaitForFirstConsumer`; the PVCs remain `Pending` until workloads
mount them in the Postgres and API checkpoints. This state is expected and does not indicate a
failed checkpoint.

Deleting either PVC is destructive. Docker Desktop's local-path volumes use a `Delete` reclaim
policy, so deleting a claim can delete its stored data.

## Checkpoint 3: Postgres

Postgres runs as a single-replica StatefulSet behind the namespace-internal, headless `postgres`
Service. It mounts the existing `postgres-data` claim and receives only its three bootstrap values
from `ooh-database`. Startup, readiness, and liveness probes use `pg_isready`.

Deploy it and wait for readiness:

```bash
./scripts/kubectl-ooh-test apply -k k8s/base
./scripts/kubectl-ooh-test rollout status statefulset/postgres --timeout=120s
./scripts/kubectl-ooh-test get pod,service,pvc
```

The StatefulSet requests 250 millicores and 1 GiB of memory, with limits of one CPU and 2 GiB.
These are initial measurement values from the Phase 3 handoff, not guaranteed production sizing.
After scheduling, `postgres-data` must be `Bound`; `ooh-cache` remains `Pending` until an
application workload mounts it.

Persistence can be checked without exposing Postgres outside the cluster:

```bash
./scripts/kubectl-ooh-test exec postgres-0 -- \
  psql -v ON_ERROR_STOP=1 -U ooh -d ooh \
  -c "CREATE TABLE IF NOT EXISTS phase3_persistence_probe (
    id integer PRIMARY KEY,
    marker text NOT NULL
  );
  INSERT INTO phase3_persistence_probe VALUES (1, 'checkpoint-3')
  ON CONFLICT (id) DO UPDATE SET marker = EXCLUDED.marker;"

./scripts/kubectl-ooh-test delete pod postgres-0
./scripts/kubectl-ooh-test rollout status statefulset/postgres --timeout=120s

./scripts/kubectl-ooh-test exec postgres-0 -- \
  psql -v ON_ERROR_STOP=1 -U ooh -d ooh \
  -c "TABLE phase3_persistence_probe;"
```

The recreated pod must return `checkpoint-3`. The probe table can then be removed:

```bash
./scripts/kubectl-ooh-test exec postgres-0 -- \
  psql -v ON_ERROR_STOP=1 -U ooh -d ooh \
  -c "DROP TABLE phase3_persistence_probe;"
```

## Checkpoint 4: database migrations

Application images are built into Docker Desktop under the project-specific local tag
`ooh-test:local`:

```bash
./scripts/k8s-build-image
```

The build command always names the `desktop-linux` Docker context and does not change the global
Docker context. Docker Desktop's `kind` provisioner has a separate containerd image store, so the
script imports the image through an ephemeral privileged helper and then removes that helper. The
project-specific `ooh-test:local` tag is the only imported tag. Kubernetes workloads use
`imagePullPolicy: Never`, so they can only run the imported image and cannot accidentally pull an
unrelated registry image with the same name.

Migrations run as the explicit, bounded `db-migrate` Job:

```bash
./scripts/k8s-run-migrations
```

The runner:

1. waits for the Postgres StatefulSet;
2. refuses to replace an active migration, otherwise removes only a previous `db-migrate` Job;
3. creates a fresh Job from `k8s/jobs`;
4. waits for successful completion and prints the migration log;
5. returns non-zero, with Job diagnostics, if migration does not complete.

This runner is the rollout gate. Application Deployments must not be applied unless it exits
successfully. The Job runs `python -m ooh.migrations`, which upgrades to the packaged Alembic head;
it then runs the existing scheduler health check to verify database reachability and exact schema
head. Running it again is a supported no-op when the schema is already current. Override the
default five-minute bound with `OOH_K8S_MIGRATION_TIMEOUT` only when a future migration is
intentionally expected to take longer.

## Checkpoint 5: API and port-forwarded UI

The API runs as a single-replica Deployment behind an internal ClusterIP Service. It mounts the
shared `ooh-cache` claim, consumes non-secret settings from `ooh-runtime`, and receives only its
database URL from `ooh-database`. Its startup and liveness probes use `/healthz`; readiness uses
`/readyz`, which checks the database, exact Alembic head, and writable cache.

Build/import the current application image, then deploy through the migration gate:

```bash
./scripts/k8s-build-image
./scripts/k8s-deploy-api
```

The API is intentionally outside `k8s/base`. The deployment script must complete
`k8s-run-migrations` successfully before it applies `k8s/apps`; a failed migration therefore cannot
roll out a new API pod. Repeated deployments restart the API so a newly imported local image is
used even though its project-local tag remains `ooh-test:local`.

The Deployment requests 100 millicores and 256 MiB of memory, with limits of 500 millicores and
512 MiB. After scheduling, both `postgres-data` and `ooh-cache` must be `Bound`.

Start the loopback-only port-forward:

```bash
./scripts/k8s-port-forward-api
```

In a second terminal, validate API, runtime configuration, and the bundled UI:

```bash
curl http://localhost:8500/healthz
curl http://localhost:8500/readyz
curl http://localhost:8500/runtime
open http://localhost:8500/
```

The wrapper fixes both sides of the forward at port `8500` and binds only `127.0.0.1`. It does not
create an Ingress, NodePort, LoadBalancer, or non-loopback listener.

## Checkpoint 6: role-specific workers

The durable Postgres job queue is consumed by three single-replica Deployments:

| Deployment | `OOH_WORKER_JOB_TYPES` | Initial resources |
| --- | --- | --- |
| `worker-ingestion` | `ingest_repository` | 250m/512Mi requested; 1 CPU/1Gi limited |
| `worker-generation` | `generate_test` | 100m/256Mi requested; 500m/512Mi limited |
| `worker-judging` | `judge_answer` | 100m/256Mi requested; 500m/512Mi limited |

There is intentionally no `compute_drift` worker. Drift calculation remains part of
`ingest_repository`, preserving the Phase 2 ownership contract.

Build/import the current application image, then deploy all three roles through the migration gate:

```bash
./scripts/k8s-build-image
./scripts/k8s-deploy-workers
```

Each worker consumes shared settings from `ooh-runtime`, receives the database URL from
`ooh-database`, and mounts `ooh-cache` at `/ooh_cache`. Startup and readiness probes run
`python -m ooh.worker.healthcheck`, which verifies database connectivity, exact Alembic head, and a
writable cache. Kubernetes already restarts a worker whose main process exits; a dependency-based
liveness probe is deliberately omitted so a temporary database outage does not create a restart
loop.

The Deployments use `Recreate`, and the deployment script restarts each pre-existing fixed
local-image tag serially. Newly created Deployments are not restarted, avoiding a race before the
worker installs its graceful-shutdown handler. This prevents old and new pods for the same role
from overlapping during rollout, particularly for ingestion's mutable per-repository checkout.
Each pod receives 660 seconds of termination grace so bounded model operations can finish and
persist their durable job outcome; the Deployment progress deadline allows for that grace period.

Inspect the role filters and readiness:

```bash
./scripts/kubectl-ooh-test get deployments,pods \
  -l app.kubernetes.io/part-of=ooh-test
./scripts/kubectl-ooh-test logs deployment/worker-ingestion --tail=20
./scripts/kubectl-ooh-test logs deployment/worker-generation --tail=20
./scripts/kubectl-ooh-test logs deployment/worker-judging --tail=20
```

Workers do not accept inbound traffic, so this checkpoint creates no Service, port-forward, or host
port. The scheduler remains a separate Deployment for checkpoint 7.

## Network and port contract

Phase 3 starts with port-forwarding. It does not create an Ingress, NodePort, or LoadBalancer
Service.

| Host port | Intended target | Use |
| ---: | --- | --- |
| `8500` | `service/api:8500` | API and bundled UI |
| `8501` | `service/postgres:5432` | Optional database diagnostics only |
| `8502`–`8510` | unassigned | Reserved for future local-only forwards |

Once the API Service exists, access it with:

```bash
./scripts/kubectl-ooh-test port-forward service/api 8500:8500
```

If direct database access is temporarily needed:

```bash
./scripts/kubectl-ooh-test port-forward service/postgres 8501:5432
```

No Kubernetes workflow in this repository may bind a host port outside `8500`–`8510`. Service
ports inside the cluster are not host-facing and keep their native values.

## Provider assumptions to validate in later checkpoints

- Docker Desktop provides the default `standard` local-path StorageClass.
- Public GitHub repositories are cloned into a shared cache volume before host repository mounts
  are introduced.
- Native Ollama remains on macOS. Workers will receive a configurable
  `OOH_OLLAMA_BASE_URL`; Docker Desktop connectivity through `host.docker.internal` must be proven
  with a running workload.
- The scheduler remains a Deployment initially. CronJobs must not run beside it.
- Every application and data resource must be created in `ooh-test`.
