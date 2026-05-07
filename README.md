# Disaster Recovery Orchestration Platform for OpenStack (FastAPI)

Automates disaster-recovery (DR) decisioning and execution for OpenStack VMs using an explicit **state machine**, a durable **audit log**, and a **job-based** async API.

This repo includes:
- A **DR control plane** (`/v1/vms`, `/v1/dr/*`, `/v1/audit`) that tracks VMs, health probes, snapshots, and DR jobs.
- A **OpenStack lifecycle** router (`/v1/servers`) that proxies a Keystone bearer token to OpenStack for create/start/stop/delete.
- A **mock adapter** for end-to-end local runs without OpenStack.


## Problem statement

An enterprise runs **200+ VMs on a private cloud** — build agents, internal services, ML notebooks, customer workloads. When one fails (hardware fault, resource exhaustion, hypervisor crash, network partition) the recovery runbook today is **manual**:

1. Notice the alert — typically a **5–15 minute lag** before a human looks at it.
2. SSH in, triage what's actually broken.
3. Manually snapshot the disk, stop the source VM, boot a copy on a standby compute host.
4. Verify the new VM is reachable; update DNS / load balancer; tell the on-call channel.

**Result: 45–90 minutes of downtime. The SLA promises 15 minutes.** Every breach is a customer-visible incident, an audit finding, and an on-call burnout multiplier.

### Why a REST API is the right shape

The fix is not "more dashboards" — it's an **opinionated control plane** that turns the runbook into a small, auditable, async API. The lifecycle maps cleanly to endpoints:

| Step in the runbook | API surface |
|---|---|
| Detect failure | `POST /v1/vms/{id}/health-check` (manual now; Celery-beat in Phase 2) |
| Capture state | `POST /v1/vms/{id}/snapshot` |
| Drive recovery | `POST /v1/vms/{id}/dr/trigger` → `202 Accepted` + `job_id` |
| Track progress | `GET /v1/dr/jobs/{job_id}` (SLA remaining, current state) |
| Prove what happened | `GET /v1/vms/{id}/audit`, `GET /v1/audit` |

Behind those endpoints sits the actual `snapshot → stop → migrate → boot → verify` pipeline, executed as an async job with retry/backoff against transient OpenStack errors.

**Phase 1 — this iteration (delivered):**

| Capability | What's delivered |
|---|---|
| Runbook mapped to API endpoints | Lifecycle + DR jobs + audit endpoints across `/v1/vms` and `/v1/dr/*` |
| Explicit VM state machine | `HEALTHY → SUSPECT → FAILING → SNAPSHOTTING → MIGRATING → RESTORING → RECOVERED \| FAILED` with illegal transitions rejected |
| DR orchestrator | `snapshot → stop → migrate → boot → verify` pipeline with per-step retry + state persistence |
| Async job model | `POST /dr/trigger` returns `202 Accepted` + `job_id`; poll via `GET /dr/jobs/{id}` |
| Idempotent triggers (in-flight) | Re-triggering an in-flight DR returns the existing `job_id` instead of duplicating |
| Per-job SLA / RTO tracking | `SLATracker` records elapsed + RTO-remaining on every poll |
| Atomic audit trail | Every state change writes `AuditEvent` in the same DB transaction (no torn states) |
| Request correlation | `X-Request-ID` middleware propagates into structured JSON logs and audit rows |
| Adapter abstraction | `VMAdapter` Protocol with `MockAdapter` (default) and `OpenStackAdapter` (real Nova/Glance) |
| Local demo without OpenStack | `ADAPTER_MODE=mock` runs the full pipeline end-to-end |
| Persistence | SQLite via async SQLAlchemy (`aiosqlite`) — same API as Postgres for Phase 2 |
| Containerized | Multi-stage `Dockerfile` (non-root, healthcheck) + `docker-compose.yml` |
| Observability hooks | Structured JSON logs + Prometheus `/metrics` |
| Stable error envelope | `{ "error": { "code", "message", "request_id" } }` with sanitized 5xx |
| Test suite | 48 tests across state machine, orchestrator, adapter, audit, and HTTP layer |

**Phase 2 — robustness & survivability:**

| Capability | This iteration | Phase 2 enhancement |
|---|---|---|
| Survivable async pipeline | FastAPI `BackgroundTasks` (in-process) | Celery + Redis workers (jobs survive restarts) |
| Persistence | SQLite via `aiosqlite` | Postgres via `DATABASE_URL` swap |
| Survive transient OpenStack failures | Retry with exponential backoff (`tenacity`) | Add circuit breaker (`pybreaker`) |
| Idempotent long-running ops | `job_id` + in-flight replay on `dr/trigger` | Full `Idempotency-Key` header (Redis-backed) |
| Per-job SLA / RTO tracking | `SLATracker` per job, surfaced on poll | RTO-breach alerts to Slack / PagerDuty |
| Auto-detect failures (no human in loop) | Manual `POST /health-check` endpoint | Celery-beat scheduler + Redis distributed lock |

**Phase 3 — production-readiness:**

| Capability | This iteration | Phase 3 enhancement |
|---|---|---|
| Auditable trail of every decision | Atomic state + `AuditEvent` (DB) | WORM storage + signed audit hashes |
| Production-grade auth / RBAC | Keystone bearer (real mode only) | OIDC for callers + API-layer RBAC |
| Traffic re-route after recovery | Out of scope | Octavia / external DNS integration |
| Distributed tracing | Structured JSON logs + `/metrics` | OpenTelemetry across FastAPI → openstacksdk |
| Deployment | Dockerfile + `docker-compose` | Helm chart with HPA / PDB / NetworkPolicy |
| Failover scope | Standby compute host (same region) | Cross-region failover with cost-aware standby selection |


## Architecture diagram

### High-level components (control plane)

```
                    +--------------------+
                    |      Client        |
                    | (Ops / CI / UI)    |
                    +---------+----------+
                              |
                              | HTTP (OpenAPI /docs)
                              v
                    +--------------------+
                    |  FastAPI app       |
                    |  app/main.py       |
                    +----+----------+----+
                         |          |
                         |          |
                         |          +-------------------------------+
                         |                                          |
                         v                                          v
            +-----------------------+                  +-----------------------+
            | DR control plane      |                  | Raw OpenStack proxy   |
            | /v1/vms /v1/dr /audit |                  | /v1/servers           |
            +-----------+-----------+                  +-----------+-----------+
                        |                                          |
                        | writes state + audit                      | uses Keystone token
                        v                                          v
            +-----------------------+                  +-----------------------+
            | Async DB (SQLAlchemy) |                  | keystoneauth1 Session |
            | VM / DRJob / Audit    |                  | + openstacksdk Conn   |
            +-----------+-----------+                  +-----------+-----------+
                        |                                          |
                        | job runner                                | OpenStack API
                        v                                          v
            +-----------------------+                     +--------------------+
            | DR Orchestrator       |                     | OpenStack services |
            | state_machine + retry |                     | (Nova/Glance/etc.) |
            +-----------------------+                     +--------------------+
```

### Execution path (DR job)

```
POST /v1/vms/{vm_id}/dr/trigger  -> 202 Accepted + job_id
                                      |
                                      v
                              Background job runner
                                      |
                                      v
 FAILING -> SNAPSHOTTING -> MIGRATING -> RESTORING -> RECOVERED
   |            |              |             |
   |            |              |             +-- ping/verify
   |            |              +-- stop source VM
   |            +-- create snapshot
   +-- audit + SLA tracking at each step
```

Notes:
- The MVP runs the pipeline via **FastAPI `BackgroundTasks`** (single-process async).
- The code is structured so Phase 2 can swap in **Celery + Redis** without changing the HTTP contract.


## API overview

OpenAPI docs:
- Swagger UI: `GET /docs`
- OpenAPI JSON: `GET /openapi.json`

### DR control-plane endpoints (core)

- **Register/list VMs**
  - `GET /v1/vms`
  - `POST /v1/vms`
  - `GET /v1/vms/{vm_id}`
  - `DELETE /v1/vms/{vm_id}`
- **Health probes**
  - `POST /v1/vms/{vm_id}/health-check`
- **Snapshots**
  - `POST /v1/vms/{vm_id}/snapshot`
  - `GET /v1/vms/{vm_id}/snapshots`
- **DR jobs**
  - `POST /v1/vms/{vm_id}/dr/trigger` → `202 Accepted` with `job_id` (idempotent replay)
  - `GET /v1/dr/jobs/{job_id}` → poll job status
  - `POST /v1/dr/jobs/{job_id}/abort` → request abort
  - `GET /v1/dr/jobs` → list recent jobs
- **Audit**
  - `GET /v1/vms/{vm_id}/audit`
  - `GET /v1/audit`

### Raw OpenStack lifecycle endpoints (token-proxy)

These require `Authorization: Bearer <KEYSTONE_TOKEN>`:
- `POST /v1/servers`
- `POST /v1/servers/{server_id}/start`
- `POST /v1/servers/{server_id}/stop`
- `DELETE /v1/servers/{server_id}`


## Local development

### Install

```bash
# 1. Create and activate a virtualenv
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

# 2. Install the project + dev tooling (pytest, ruff, black, mypy)
pip install -e ".[dev]"
```

### Run

Mock mode (default — no OpenStack required):

```bash
# Windows (PowerShell):  $env:ADAPTER_MODE = "mock"
# macOS / Linux:         export ADAPTER_MODE=mock
uvicorn app.main:app --reload --port 8000
```

Or with Docker:

```bash
docker compose up --build
```

### Try it end-to-end (mock mode)

Once the server is up, this sequence registers a VM, triggers a DR pipeline, and polls the job to completion — entirely against the mock adapter.

> **See [docs/SAMPLE_RUN.md](docs/SAMPLE_RUN.md)** for a verbatim PowerShell transcript of this flow against the mock adapter, including the DR job response, the full audit trail, and an annotated timeline showing how the state machine and `X-Request-ID` correlation behave end-to-end.

**Windows (PowerShell):**

```powershell
# 1. Register a VM for DR monitoring
$body = @{ name = "build-agent-7"; external_id = "ext-build-7"; rto_minutes = 15 } | ConvertTo-Json
$vm   = Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/vms `
          -ContentType 'application/json' -Body $body
$vmId = $vm.id

# 2. Trigger the DR pipeline (returns 202 + job_id)
$job   = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/v1/vms/$vmId/dr/trigger" `
            -ContentType 'application/json' -Body '{}'
$jobId = $job.id

# 3. Poll the job to completion
Invoke-RestMethod -Uri "http://localhost:8000/v1/dr/jobs/$jobId" | ConvertTo-Json -Depth 10

# 4. Inspect the audit trail
Invoke-RestMethod -Uri "http://localhost:8000/v1/vms/$vmId/audit" | ConvertTo-Json -Depth 10
```

### Test

```bash
pytest                                  # full suite (48 tests)
```

## Design choices & trade-offs

Each decision below is named with the alternative considered and the trade-off behind the choice.

### 1. Explicit state machine over free-form status strings
Recovery has eight states with strict ordering. A passthrough `status: str` field would have been faster but invites bugs like *"snapshotting a recovered VM"* or *"recovered without restoring"*. Encoding states as a `VMState` enum with a `TRANSITIONS` table makes illegal transitions unrepresentable and makes audit rows query-friendly (`from_state`, `to_state`).

### 2. Async job model (`202 + job_id`) over a blocking `POST`
A DR pipeline takes minutes, not milliseconds. A blocking `POST` would tie up workers, time out at proxies, and cause the client to retry — creating duplicate pipelines. Returning `202 Accepted` + `Location: /v1/dr/jobs/{id}` decouples the request from the work, lets the caller poll, abort, or retry safely, and is the standard for any operation that doesn't fit in a single HTTP timeout.

### 3. Adapter Protocol with mock + real implementations
The platform must be runnable end-to-end without provisioning OpenStack. The `VMAdapter` Protocol lets `MockAdapter` (in-memory, deterministic, latency-tunable) and `OpenStackAdapter` (real `openstacksdk` calls via `asyncio.to_thread`) coexist behind the same orchestrator. Cost: duplicated method signatures. Benefit: the orchestrator is 100% testable with zero infra.

### 4. State + audit written in one transaction
Every state mutation in `DROrchestrator._step` writes the VM row update *and* the `AuditEvent` in the same `AsyncSession.commit()`. If anything throws between them, neither is persisted — there's no such thing as a state change without an audit row, or vice versa. This is the property compliance auditors care about most.

### 5. `BackgroundTasks` (not Celery) for the MVP
Celery + Redis is the right answer for production. At the MVP stage it adds two services without changing the HTTP contract, so the cost outweighs the benefit. The `_run_pipeline` callsite is a single function, which keeps the Phase 2 swap to one file. **Honest cost:** a process restart loses an in-flight pipeline; the `DRJob` row remains, so the orphan is detectable and the operator can re-trigger.

### 6. Sanitized error envelope with stable codes
4xx responses forward the provider message (caller-actionable). 5xx responses return only `{ "error": { "code": "...", "message": "...", "request_id": "..." } }` with a default message — raw OpenStack tracebacks are logged but never leak in the response body. CI/CD pipelines branch on `code`; humans correlate via `request_id` in logs.


## Limitations (intentional for MVP)

- **Health monitor auto-trigger is not a background scheduler yet**
  - Health probes can be triggered via API; there is not yet a Celery beat / cron loop that checks all VMs.
- **BackgroundTasks is not survivable across restarts**
  - If the process restarts mid-DR, the in-flight background job is lost (DB state remains).
- **No distributed locking**
  - In a multi-worker deployment, two workers could trigger DR for the same VM without a lock.
- **OpenStack “ping” is best-effort in real mode**
  - `OpenStackAdapter.ping_vm()` currently checks server status; a real probe should hit an app port (HTTP/SSH) or a health agent.
- **Network reroute (DNS/LB) not implemented**
  - The orchestration focuses on snapshot/stop/boot/verify; traffic switching is a Phase 2/3 integration.
- **Data-plane decisions are simplified**
  - Standby selection uses a best-effort heuristic; policy/cost/affinity rules are not implemented.


## Roadmap detail

The Phase 2 and Phase 3 tables earlier summarize *what* changes; the items below add the *how* and *why* for each phase.

### Phase 2 — robustness & survivability

- **Survivable async pipeline.** Replace FastAPI `BackgroundTasks` with Celery workers so DR jobs survive process restarts. Same HTTP contract (`202 + job_id + poll`).
- **Postgres.** Swap SQLite for `asyncpg` in deployment; same async SQLAlchemy API.
- **Auto-detection.** Celery-beat task probes every registered VM on a schedule; threshold-based transitions (`HEALTHY → SUSPECT → FAILING`) auto-trigger DR.
- **Distributed lock.** Redis lock keyed by VM id so only one DR pipeline can run per VM across workers.
- **Richer probes.** Pluggable TCP/HTTP/agent-based probes per VM, not just "Nova thinks it's `ACTIVE`".
- **Reliability.** Circuit breaker (`pybreaker`) in front of OpenStack calls; full `Idempotency-Key` header for any mutation.

### Phase 3 — production-readiness

- **DNS / load balancer rerouting.** Integrate Octavia/Neutron (or external DNS/LB) to actually shift traffic to the recovered VM.
- **Policy engine.** Per-VM rules — approval gates, DR-allowed time windows, max cost per recovery, preferred standby pools, cost-aware standby selection.
- **Auth & RBAC.** OIDC for human callers, application credentials for service-to-service, API-layer role checks beyond what Keystone enforces.
- **Tracing.** OpenTelemetry across FastAPI → openstacksdk so a span shows *"this DR was slow because Nova was slow"*.
- **Audit hardening.** WORM storage, signed audit hashes, per-action subject (token `sub`) on every event.
- **Cross-region failover** instead of just a standby compute host.
- **Helm chart** with HPA, PDB, NetworkPolicy, ServiceMonitor, ExternalSecrets.


## Environment variables (high signal)

- `ADAPTER_MODE`: `mock` (default) or `openstack`
- `DATABASE_URL`: defaults to SQLite in docker-compose; set for real deployments
- `DEFAULT_RTO_MINUTES`: default RTO for newly-registered VMs
- `MOCK_LATENCY_MS`: mock adapter latency tuning
- `OPENSTACK_AUTH_URL`: required for `ADAPTER_MODE=openstack` token-proxy mode


## Key references in the codebase

- **API wiring**: `app/main.py`
- **DR orchestrator**: `app/core/orchestrator.py`
- **State machine**: `app/core/state_machine.py`
- **Adapters**: `app/adapters/mock.py`, `app/adapters/openstack.py`, `app/adapters/factory.py`
- **Routers**: `app/api/routers/vms.py`, `app/api/routers/dr.py`, `app/api/routers/audit.py`, `app/api/routers/health.py`, `app/api/routers/servers.py`

