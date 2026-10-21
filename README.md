# PatchLens

Repository-aware, AI-assisted code review with test intelligence.

PatchLens reviews a pull-request diff the way a careful reviewer with repository
access would: it finds the changed symbols, retrieves the callers, tests and docs
that matter, runs deterministic checks, asks an LLM for judgement calls, and then
verifies every location it wants to publish before it publishes anything.

The project is built to be honest about what it knows. Findings are labelled
`verified`, `ai_suggested`, `needs_manual_verification` or `rejected`; a finding
that cannot be anchored to a line the diff actually added is discarded rather than
shown with an invented location. Runs without paid credentials use a deterministic
mock provider that is labelled as a mock in the API, the CLI and the UI model.

## Status

| Area | State |
| --- | --- |
| Milestone 1 — foundation, settings, DB, durable job queue, worker | done |
| Milestone 2 — Python AST ingestion, versions, embeddings, test mapping | done |
| Milestone 3 — diff → retrieval → checks → LLM → validation → API/CLI | done |
| Milestone 4 — retrieval evaluation (metrics, datasets, runner) | done — first runs recorded |
| Milestone 5 — GitHub App webhooks and comment publication | done — tested against a mocked GitHub API |
| Milestone 6 — test generation and isolated execution | done — disabled, subprocess and Docker tiers; Docker tier exercised |
| Milestone 7 — reliability/security hardening pass | done — retries, timeouts, dedupe and failure injection; no load/soak run |
| Milestone 8 — web frontend, docs, screenshots, demo polish | done — Next.js dashboard, 12 screenshots, portfolio bullets |

Verified on 2026-10-21: **455 backend tests**, **87% statement coverage**, **26
frontend tests**, ruff and mypy strict clean, the frontend production build, and the
five-service Compose stack with PostgreSQL 16 + pgvector and Redis. The Docker test
runner executed a generated candidate (exit 0, 3 tests, 545 ms) with the network
disabled. Remaining limits are listed at the end of this file; every recorded number
is tied to a command or stored run id.

## Quick start (no Docker, no credentials)

Requires Python 3.11.

```bash
make setup          # create .venv and install the locked dependencies
make init-db        # create a SQLite database under backend/var/
make demo-repo      # register + index examples/demo-repo (a real fixture project)
make demo-review    # review examples/demo-repo/pr.diff
```

`make demo-review` prints findings with their rule id, verification status and
anchor. With the default mock provider the output is deterministic, and every
finding is derived from the diff and the indexed repository — not generated prose.

Inspect the same data over HTTP:

```bash
make run            # http://127.0.0.1:8000  (OpenAPI at /docs)
curl -s localhost:8000/api/v1/meta | jq
curl -s localhost:8000/api/v1/repositories | jq
```

Start a worker to process queued jobs (the API queues reviews by default):

```bash
make worker
```

Everything above uses SQLite, a database-backed job queue, the deterministic mock
LLM and deterministic hashing embeddings. No external services are required.

### Dashboard

The Next.js app talks to the API through a same-origin proxy, so the API token is
never shipped to the browser. Keep the API running, then:

```bash
make frontend-install          # npm install in frontend/
PATCHLENS_API_URL=http://127.0.0.1:8000 make frontend-dev
# http://127.0.0.1:3000
```

If `API_AUTH_TOKEN` is set, also export `PATCHLENS_API_TOKEN` for the frontend
process. The proxy at `/api/backend/*` injects it server-side. `make frontend-lint`,
`make frontend-test` and `make frontend-build` run the quality gates.

### Full stack with Docker

```bash
make compose-up               # PostgreSQL 16 + pgvector, Redis, migrate, API, worker
curl -s localhost:8000/readyz
docker compose ps             # api healthy; worker up (no HTTP probe)
```

The migration container is the only schema owner (`AUTO_CREATE_SCHEMA=false`). The
API and worker share a non-root image; the worker uses Redis as a wake-up hint and
still polls the durable `jobs` table. Demo fixtures are mounted read-only, so the
same commands work inside the API container. The test-runner image is separate:

```bash
make test-runner-image        # docker build -t patchlens-test-runner:latest docker/test-runner
```

## What a review actually does

```mermaid
flowchart LR
    A[diff] --> B[parse + validate<br/>paths, hunks, added lines]
    B --> C[changed symbols<br/>reparse the new side]
    C --> D[hybrid retrieval<br/>BM25 + semantic + RRF]
    D --> E[deterministic checks<br/>line + AST rules]
    D --> F[LLM provider<br/>labelled mock or OpenAI-compatible]
    E --> G[validation boundary<br/>path, line, evidence, dedupe]
    F --> G
    G --> H[(reviews / findings / feedback)]
    G --> I[publisher<br/>GitHub comments + neutral check]
```

Two properties are enforced in code rather than documented as intentions:

1. **No invented locations.** Every persisted finding must point at a line the diff
   added, an existing path inside the repository, and evidence that exists in the
   reviewed content. Rejected candidates keep the reason, so the trace endpoint can
   explain why a suggestion was thrown away.
2. **Degradation is explicit.** If embeddings fail, retrieval falls back to lexical
   search and the review records a warning. If the LLM fails, deterministic
   findings are still persisted and the review is marked failed/degraded with the
   provider error. Neither case silently produces a smaller, unlabelled result.

## Repository intelligence

- Python is parsed with `ast`: modules, classes, functions, methods, docstrings,
  signatures, line ranges, imports, call edges and test functions.
- Each index run creates an immutable `repository_version` keyed by commit SHA (or
  a content hash for local checkouts). Re-indexing an unchanged tree short-circuits.
- Embeddings are content-addressed, so unchanged symbols are reused across versions
  instead of re-embedded.
- Test files are structurally mapped to source symbols (imports, mirrored package
  paths, name pairs, call edges) with fixed, documented weights.
- Vendored/build/cache directories, binary files and secret-looking dotfiles are
  excluded before parsing.

## Retrieval

Five strategies share one interface so they can be compared on the same corpus:

| Strategy | What it does |
| --- | --- |
| `static` | exact identifier/path matching (unranked baseline) |
| `bm25` | BM25 Okapi implemented in-process (`k1=1.5`, `b=0.75`) |
| `semantic` | cosine similarity over stored vectors |
| `hybrid` | reciprocal rank fusion of BM25 and semantic (`k=60`) |
| `hybrid_rerank` | hybrid candidates re-scored by a documented heuristic reranker |

The reranker is a weighted heuristic, **not** a trained cross-encoder; the code and
the API say so. On SQLite, semantic search is an in-process scan (`portable_scan`);
on PostgreSQL it uses pgvector's cosine-distance operator (`<=>`) inside the
database. The column is still unconstrained (`vector` without a dimension) and there
is no HNSW index yet, so the Postgres path is an exact scan, not an ANN query. This
path was executed against PostgreSQL 16 + pgvector on 2026-10-21; `/api/v1/settings`
reported `semantic_backend=pgvector`.

Embeddings have two providers: `hashing` (deterministic feature hashing, explicitly
non-neural) and `openai_compatible` (a real neural embedding endpoint, opt-in with
credentials). Each stored embedding records which one produced it, so a mock/hashing
index can never masquerade as a neural one.

## Evaluation

`backend/eval/datasets/` holds three versioned, content-hashed datasets: 12 graded
retrieval queries, 3 labelled diffs (the demo PR, a clean negative control, and an
off-by-one logic bug the rules cannot see), and 3 test-generation targets.
`make eval-seed && make eval-run` records the runs; the API serves the same stored
rows.

Recorded runs on the bundled demo repo. The current runs use PostgreSQL 16 +
pgvector (2026-10-21); the earlier SQLite runs are kept for comparison in
[docs/EVALUATION.md](docs/EVALUATION.md).

| Run | Environment | Result |
| --- | --- | --- |
| retrieval — 12 queries, five strategies | PostgreSQL + pgvector | best nDCG@10 `hybrid_rerank` 0.833; MRR@10 0.938; Recall@5 0.799; 0 failures |
| review — 3 diffs, mock provider | PostgreSQL + pgvector | precision 1.000, recall 0.929 (13 tp, 0 fp, 1 fn); must-find recall 0.857 |
| test generation — 3 targets, mock provider | PostgreSQL + pgvector | validity 3/3 (1.000); execution `UNKNOWN` because the sandbox was disabled |

Run ids: `evr_1c874fcd28be4d27adb12542972fb79a`,
`evr_a473310248634f82b8585d100bf75434` and
`evr_35a3d64206234b39b3d50c03834bf1ad`. The full serializer payloads are checked in
under `docs/eval/2026-10-21-*.json`.

The single review miss is reported rather than tuned away: an off-by-one in `mean()`
is a semantic defect no deterministic rule detects. Cost is `UNKNOWN` (no priced
provider was configured). These numbers measure the deterministic layer with a
labelled rule-based provider and a non-neural embedding signal — not an LLM. The
retrieval dataset labels refer to `demo-local` while the Compose corpus was indexed
as `compose-demo-v1`; the run records that mismatch as a limitation. Full tables,
dataset hashes, per-case analysis and reproduction commands are in
[docs/EVALUATION.md](docs/EVALUATION.md). Undefined metrics render as `UNKNOWN`; a
dataset without a run is `NOT RUN`.

## Test intelligence

PatchLens can generate candidate regression tests for changed or indexed symbols
and execute them only when an execution tier is explicitly enabled. Candidates store
their target, source, suggested path, provider/model and a status; generation alone
never sets `executed_at` or a pass result.

| Tier | Behaviour |
| --- | --- |
| `disabled` (default) | Candidates are stored as `execution_disabled` with the reason. No process is started. |
| `subprocess` | Requires `TEST_EXECUTION_MODE=subprocess` **and** `ALLOW_LOCAL_TEST_EXECUTION=true`. Timeout, sanitised environment and best-effort POSIX limits; **not a security boundary**. |
| `docker` | `TEST_EXECUTION_MODE=docker` with `patchlens-test-runner:latest`. Network off by default, repository and test mounts read-only, `--cap-drop ALL`, `no-new-privileges`, read-only root, tmpfs, memory/CPU/PID limits, timeout and output caps. |

Build the runner once:

```bash
make test-runner-image         # docker build -t patchlens-test-runner:latest docker/test-runner
TEST_EXECUTION_MODE=docker make demo-tests
```

The Docker tier was exercised on 2026-10-21: candidate
`tst_437cecac3e1c40c6a7a383fbbb003025` finished `passed` with exit 0, 3 collected
tests and 545 ms, with `sandbox_mode=docker` and the network disabled. If the Docker
CLI or daemon is unavailable, the candidate is marked `execution_disabled` with the
probe error; a missing image or container failure is recorded as `error` with the
output — never `passed`. The evaluation harness reports execution metrics as
`UNKNOWN` unless a sandbox is enabled, and its bundled mock generator produces an
importability smoke suite rather than a behavioural test.

## GitHub integration

GitHub App webhooks are verified against the raw body (`X-Hub-Signature-256`) and
deduplicated by `X-GitHub-Delivery`. `pull_request` `opened`/`reopened`/`synchronize`
events resolve a registered local checkout, queue one review per head SHA, fetch the
diff with an installation token, run the normal review pipeline, and publish:

- one pull-request **review with `event: COMMENT`** — never an approval or a change
  request;
- inline comments only for `verified` and `needs_manual_verification` findings, only
  configured severities, verified added-line anchors and safe paths, capped by
  `GITHUB_MAX_COMMENTS_PER_REVIEW`;
- a **neutral** check run whose annotations mirror the published findings.

Every finding records `publish_state` (`published` / `skipped` / `failed`) with a
reason, and comment text excludes raw code excerpts and passes secret redaction.
The App asks for metadata read, pull-requests write and checks write — no contents,
code write, workflows, secrets or administration. Setup, permissions, failure
semantics and the untested paths are in [docs/GITHUB.md](docs/GITHUB.md).

The local demo does not need a GitHub App: `make demo-review` exercises the same
pipeline with publication recorded as `skipped`. No live GitHub App installation has
been exercised here; the integration tests drive the real webhook route, queue,
worker and publisher against an `httpx.MockTransport` GitHub API and a generated RSA
key.

## Findings and feedback

Each finding carries: category, severity, rule id, detector (`static`, `static_ast`,
`llm`), verification status, confidence basis, path, line range, evidence and a
rejection reason when it was discarded. Feedback labels (`correct`, `incorrect`,
`irrelevant`, `duplicate`, `needs_investigation`) are stored per finding and
aggregated; nothing is retrained from them automatically.

`GET /api/v1/findings/{id}/trace` returns the full story: the rule definition, the
detector, whether the cited line was verified against the diff, the evidence, and a
`not_executed` list naming what PatchLens did **not** verify (for example "no test
or execution was performed for this finding; verification is static").

## API

All routes are under `/api/v1` except the probes. `Authorization: Bearer <token>` is
required when `API_AUTH_TOKEN` is set; `/healthz`, `/readyz`, `/api/v1/meta` and the
HMAC-authenticated `POST /api/v1/github/webhooks` remain public so an orchestrator
can probe the process, a UI can discover that a token is required, and GitHub can
deliver events without a bearer token.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz`, `/readyz` | liveness, readiness (DB included) |
| GET | `/api/v1/meta` | version, capabilities, provider mode, rule catalogue |
| GET | `/api/v1/overview` | dashboard aggregates (evaluation runs excluded) |
| GET/POST | `/api/v1/repositories` | list / register a local checkout (development only) |
| GET/DELETE | `/api/v1/repositories/{id}` | detail / delete with its data |
| POST | `/api/v1/repositories/{id}/index` | queue an index job |
| GET | `/api/v1/repositories/{id}/versions` | index snapshots |
| GET | `/api/v1/repositories/{id}/symbols` | search symbols in the latest snapshot (409 without one) |
| POST | `/api/v1/reviews` | review a diff (`wait=true` runs in-request) |
| GET | `/api/v1/reviews`, `/api/v1/reviews/{id}` | list / detail |
| GET | `/api/v1/reviews/{id}/findings` | findings for a review |
| GET | `/api/v1/reviews/{id}/test-candidates` | candidate tests generated for a review |
| GET | `/api/v1/findings/{id}` | finding detail |
| GET | `/api/v1/findings/{id}/trace` | why the finding exists, and what was not checked |
| POST/GET | `/api/v1/findings/{id}/feedback` | record / list feedback |
| GET | `/api/v1/findings/feedback/aggregate` | aggregate label counts |
| GET | `/api/v1/evaluation/datasets`, `/api/v1/evaluation/datasets/{id}` | labelled datasets and their cases |
| GET/POST | `/api/v1/evaluation/runs` | list runs / run a dataset synchronously |
| GET | `/api/v1/evaluation/runs/{id}` | aggregate and per-case results |
| GET | `/api/v1/test-execution` | effective sandbox tier, availability and limits |
| GET | `/api/v1/test-candidates`, `/api/v1/test-candidates/{id}` | list / inspect generated candidates |
| POST | `/api/v1/repositories/{id}/test-candidates` | generate candidates for indexed symbols |
| POST | `/api/v1/test-candidates/{id}/execute` | execute in the configured sandbox (202) |
| GET | `/api/v1/jobs`, `/api/v1/jobs/{id}` | durable job state and events |
| POST | `/api/v1/jobs/{id}/cancel` | cancel a queued job |
| POST | `/api/v1/github/webhooks` | GitHub webhook receiver (HMAC signature required) |
| GET | `/api/v1/github/installations` | installations observed from webhook events |
| GET | `/api/v1/github/deliveries` | webhook audit rows, optionally filtered by `status` |
| GET | `/api/v1/settings` | effective configuration, secrets masked |

Errors use one shape: `{"error": {"code", "message", "details"}}`, with codes such
as `validation_error`, `not_found`, `unauthorized`, `payload_too_large`,
`provider_error`.

## Configuration

Every setting has a working default. Copy `.env.example` to `.env` to change them;
the example file is generated from the settings model (`make env-example`, or
`python -m patchlens.cli config --write-env-example`).

| Variable | Default | Notes |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `production` forbids local checkout registration |
| `DATABASE_URL` | SQLite under `backend/var/` | `postgresql+psycopg://...` for Postgres |
| `REDIS_URL` | unset | optional wake-up channel; jobs live in the DB |
| `AUTO_CREATE_SCHEMA` | `true` | ignored in production; use Alembic there |
| `API_AUTH_TOKEN` | unset | when set, protects every data route |
| `LLM_PROVIDER` | `mock` | `mock` or `openai_compatible` |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | OpenAI defaults | used only by the real provider |
| `EMBEDDING_PROVIDER` | `hashing` | `hashing` (non-neural) or `openai_compatible` |
| `EMBEDDING_DIM`, `EMBEDDING_MODEL` | `512`, `hashing-ngram-v2` | changing these requires re-indexing |
| `RETRIEVAL_STRATEGY` | `hybrid` | one of the five strategies above |
| `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET` | unset | GitHub App credentials; without all three the webhook returns 502 |
| `GITHUB_PUBLISH_ENABLED` | `false` | comments/check run are created only when explicitly enabled |
| `GITHUB_COMMENT_SEVERITIES` | `critical,high` | severity filter for inline comments |
| `GITHUB_MAX_COMMENTS_PER_REVIEW` | `10` | bounded comment count (1–50) |
| `GITHUB_REQUIRE_VERIFIED_ANCHORS` | `true` | publish only added-line-verified anchors |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | configurable for GitHub Enterprise; only the mock base URL is tested |
| `TEST_GENERATION_ENABLED` | `false` | enables candidate generation (the mock is free; a real provider may charge) |
| `TEST_EXECUTION_MODE` | `disabled` | `disabled`, `subprocess` or `docker` |
| `ALLOW_LOCAL_TEST_EXECUTION` | `false` | required acknowledgement for subprocess mode; not a sandbox |
| `TEST_EXECUTION_DOCKER_IMAGE` | `patchlens-test-runner:latest` | must contain pytest and the project's runtime dependencies |
| `TEST_EXECUTION_TIMEOUT_SECONDS` | `120` | wall-clock limit for one candidate run |
| `TEST_EXECUTION_MEMORY_MB`, `TEST_EXECUTION_CPU_SECONDS` | `1024`, `60` | sandbox resource limits |
| `TEST_EXECUTION_NETWORK_ENABLED` | `false` | keep `false` unless a test genuinely needs network |

The frontend uses two server-only variables, read by the Next.js proxy rather than
the backend: `PATCHLENS_API_URL` (default `http://127.0.0.1:8000`) and
`PATCHLENS_API_TOKEN` (default unset). They are never exposed as `NEXT_PUBLIC_*`.
Empty optional backend values such as `API_AUTH_TOKEN=` and `REDIS_URL=` are
normalised to unset, so a blank line in `.env` cannot create an empty bearer token.

The provider split is deliberate: the app runs, indexes and reviews without paid
credentials, and `is_mock_provider` is stored on each review so no surface can
present mock output as a real model's work. Set `LLM_PROVIDER=openai_compatible`
plus an API key and model to use a real model.

## Quality gates

```bash
make lint             # ruff format --check + ruff check (backend, tests, migrations)
make typecheck        # mypy (strict) over the backend package
make test             # pytest
make test-cov         # pytest with a coverage report
make frontend-lint    # next lint + tsc --noEmit
make frontend-test    # vitest
make frontend-build   # next build
make ci               # backend + frontend gates
```

Current measured state on this machine (Python 3.11; backend tests on SQLite):
**455 backend tests pass**, **87% statement coverage** (8784 statements, 892
missed), `ruff` and `mypy --strict` clean; **26 frontend tests pass**, and lint,
typecheck and the production build succeed. The Compose stack was exercised
separately against PostgreSQL 16 + pgvector and Redis. Run the commands yourself;
the numbers in this file are updated only after the commands have been run.

## Repository layout

```
backend/
  patchlens/
    api/            FastAPI app, routes, dependencies, error mapping
    core/           settings, enums, errors, security, logging
    db/             SQLAlchemy models, session, custom types, migrations helpers
    jobs/           durable queue, worker loop, job handlers
    services/
      ingestion/    discovery, Python AST parser, indexer
      retrieval/    BM25, semantic, fusion, service, strategies
      review/       diff parser, symbol mapping, checks, prompting, validation, pipeline
      llm/          provider interface, mock, OpenAI-compatible client
      embedding/    hashing and OpenAI-compatible embedding providers
      testing/      candidate generation, sandbox resolution, execution
      github/       GitHub client, webhook ingestion, publisher
    cli.py          operational CLI
  migrations/       Alembic revisions
  tests/            unit, integration, security
frontend/           Next.js App Router dashboard (TypeScript strict, Tailwind)
docker/test-runner/ minimal pytest image for the Docker execution tier
examples/demo-repo/ a small fixture service and a real diff used by `make demo-*`
docs/               architecture, security, evaluation, GitHub integration,
                    screenshots and stored evaluation payloads
```

## Screenshots

Captured from the running dashboard against the local SQLite demo workspace (mock
provider; the evaluation page shows stored runs).

![Overview](docs/screenshots/overview.png)

| View | File |
| --- | --- |
| Overview | [overview.png](docs/screenshots/overview.png) |
| Repositories / detail | [repositories.png](docs/screenshots/repositories.png), [repository-detail.png](docs/screenshots/repository-detail.png) |
| Reviews / detail | [reviews.png](docs/screenshots/reviews.png), [review-detail.png](docs/screenshots/review-detail.png) |
| Findings / detail | [findings.png](docs/screenshots/findings.png), [finding-detail.png](docs/screenshots/finding-detail.png) |
| Test intelligence / detail | [tests.png](docs/screenshots/tests.png), [test-detail.png](docs/screenshots/test-detail.png) |
| Evaluation / run | [evaluation.png](docs/screenshots/evaluation.png), [evaluation-run.png](docs/screenshots/evaluation-run.png) |
| Settings | [settings.png](docs/screenshots/settings.png) |

Portfolio-ready bullets with measured numbers are in
[docs/RESUME.md](docs/RESUME.md).

## Honest limitations

- **No live GitHub run.** The webhook receiver, App JWT/installation-token flow,
  diff fetch, comment publication and neutral check run are implemented and covered
  by integration tests against a mocked GitHub API (`httpx.MockTransport`) with a
  generated RSA key. No real GitHub App installation has delivered an event here, and
  GitHub Enterprise is untested. Publication is off unless
  `GITHUB_PUBLISH_ENABLED=true`; without a publisher the review records
  `publish_state=skipped` with the reason. A crash after GitHub accepts a review but
  before the DB commit can duplicate the review (GitHub has no idempotency key);
  this is documented in [docs/GITHUB.md](docs/GITHUB.md).
- **No remote cloning.** Webhook review requires a registered local checkout and a
  successful index; unregistered/uncheckout repositories are recorded as failed
  deliveries rather than cloned.
- **PostgreSQL + pgvector is executed, but not ANN-indexed.** The Compose stack ran
  the migrations and retrieval against PostgreSQL 16 + pgvector. The vector column is
  unconstrained and there is no HNSW index, so semantic search is an exact cosine
  scan inside PostgreSQL. The fixed-dimension column + HNSW index is a planned
  migration.
- **The Docker sandbox is defense in depth, not a proof.** It uses a minimal pytest
  image, no network by default, read-only repository/test mounts, dropped
  capabilities, a read-only root filesystem, a tmpfs, memory/CPU/PID limits, a
  timeout and output caps. It still relies on the host Docker daemon and the default
  seccomp profile; do not run untrusted code on a host you do not trust. The
  `subprocess` tier is explicitly not a security boundary.
- **No load or soak test.** Recorded latencies are warm, single-process
  measurements, not capacity evidence. No throughput, concurrency or long-running
  stability claim is made.
- **Evaluation is deliberately small and mock-based.** The recorded review and test
  generation runs use the deterministic mock provider; retrieval uses non-neural
  hashing embeddings; labels are author-written over a 21-symbol fixture and three
  diffs. The retrieval dataset labels refer to `demo-local` while the Compose corpus
  is `compose-demo-v1`; the run records that mismatch. The numbers are real,
  reproducible and published with their limits (run ids and dataset hashes in
  `docs/EVALUATION.md`) — they are not evidence of LLM review quality.
- **No independent label review.** The evaluation labels were written by the author
  and have not been adjudicated by a second reviewer.
- **The mock provider is not AI.** It is a deterministic rule-based responder used
  to exercise the pipeline without credentials. Its output is labelled `mock`
  everywhere, including `reviews.is_mock_provider` and the API metadata.
- **Hashing embeddings are not semantic.** They are a deterministic lexical-ish
  signal for local development; the neural provider exists but has not been run here.
- **The heuristic reranker is not trained.** It applies fixed weights and is
  described as such in code and in `/api/v1/meta`.
- **Portfolio context.** This is a portfolio project, not a production service. The
  dashboard screenshots were captured against the local SQLite demo with the mock
  provider.
