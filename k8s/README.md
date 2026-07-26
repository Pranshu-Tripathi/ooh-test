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

## Checkpoint 1: provider and isolation

Prerequisites:

- Docker Desktop is running.
- Kubernetes is enabled in Docker Desktop.
- `kubectl config get-contexts docker-desktop` returns the Docker Desktop context.
- `kubectl --context docker-desktop get nodes` reports a ready node.

Create or reconcile the project namespace:

```bash
./scripts/kubectl-ooh-test apply -k k8s/base
./scripts/kubectl-ooh-test get namespace ooh-test
```

At this checkpoint, the namespace is the only runtime resource. Compose remains usable and no
application workload has moved to Kubernetes yet.

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
