# PatchLens - Task Board

Repository-aware, AI-assisted code review with test intelligence.
Status legend: `[x]` done and verified · `[~]` in progress · `[ ]` not started · `[!]` blocked.

Verification notes record exactly what was run and observed. A task is only marked
`[x]` when its acceptance criteria were exercised, not merely written.

## Milestone 1 - Foundation
Acceptance: project boots with no external services; lint/type checks pass; DB
schema creatable on SQLite; durable job queue with dedupe/retries.

- [x] Repo scaffold, git init, Python 3.11 venv, pinned lockfiles
      (`backend/requirements.in`, `requirements-dev.in`, `requirements.lock.txt`,
      `requirements-dev.lock.txt`; both compiled with pip-tools)
- [x] `backend/pyproject.toml` (ruff, mypy strict, pytest markers, coverage config)
- [x] Settings layer with working local defaults for every external dependency
- [x] Structured JSON/KV logging with secret redaction and request/job context
- [x] Typed application errors + FastAPI error handlers (numeric 422/413)
- [x] SQLAlchemy 2.x models for 18 tables; `create_all` verified on SQLite
- [x] Durable job queue (`jobs`/`job_events`): dedupe slot, attempts, exponential
      backoff, lease expiry recovery, cancellation, per-kind handlers
- [x] Worker process with lease/heartbeat, timeout enforcement, concurrency rules
- [x] Alembic initial migration `ca1e634ae27e`; verified `upgrade head`,
      `alembic check` ("No new upgrade operations detected"), `downgrade base`

## Milestone 2 - Repository intelligence
Acceptance: real Python repo indexed via AST with functions/classes/tests,
docstrings, line ranges, imports; incremental re-index by tree hash; exclusions.

- [x] Discovery/walker with exclusion of venvs, build artefacts, binary and secret files
- [x] AST parser: symbols, signatures, docstrings, calls, imports, line ranges, tests
- [x] Versioned snapshots keyed by commit SHA; unchanged-tree short circuit
- [x] Cross-version embedding reuse (`CodeEmbedding` unique on content hash + provider + model)
- [x] Structural test-to-source mapping with documented heuristic weights
- [x] Verified on this repository: 34 files / 298 symbols / 298 embeddings; second run
      `up_to_date=True`; cross-version reused 93/93 embeddings. Bundled demo repo:
      5 files / 21 symbols / 5 tests / 21 embeddings.

## Milestone 3 - Review vertical slice
Acceptance: diff -> symbols -> retrieval -> deterministic checks -> LLM ->
validated findings -> persisted -> API/CLI; findings only on added lines; mock
provider never labelled verified.

- [x] Defensive unified-diff parser (renames, binary, missing newlines, malformed and
      under-counted hunk headers; the last case keeps the extra lines and warns instead
      of silently truncating the new side)
- [x] Diff -> changed-symbol mapping. New-side content is read from the checkout when
      it matches the diff, reconstructed by applying the hunks when the checkout is at
      the base revision, and reported as a failure when it matches neither side
- [x] Deterministic checks: 25 catalogue rules (18 line implementations, 8 AST
      implementations; `http-call-without-timeout` has both and the catalogue lists
      it once). Rule metadata agreement between duplicate implementations is tested
- [x] LLM provider separation: labelled deterministic mock + OpenAI-compatible provider
- [x] Prompt assembly with untrusted-content fencing and secret redaction
- [x] Validation boundary: schema, path, added-line, range, evidence-excerpt checks,
      dedupe against deterministic and model findings, prompt-injection rejection
- [x] Review persistence with stage/progress commits, honest failure/degraded states
- [x] FastAPI app: health/ready, meta, repositories, reviews, findings, jobs, settings
- [x] CLI: `serve`, `worker`, `init-db`, `repo-add`, `repo-list`, `index`, `review`,
      `jobs`, `config`
- [x] Feedback labels (correct/incorrect/irrelevant/duplicate/needs investigation) + aggregate
- [x] Backend test suite: 318 tests across unit/integration/API/security, 88% statement
      coverage (`pytest --cov`), ruff and mypy strict clean
- [x] Local demo without GitHub: `make demo-repo` + `make demo-review` produce 12
      findings (4 verified) on the bundled PR diff
- [x] `.env.example` generated from the settings model: no machine-specific paths, no
      secret values (tests pin both properties)
- [x] `Makefile` entry points (setup, lock, env-example, lint, typecheck, test, run,
      worker, migrations, demo, compose)
- [x] GitHub Actions CI: backend lint/type/test + migrations upgrade/check/downgrade
      jobs (written; not yet executed on GitHub)
- [x] `docker-compose.yml` + `backend/Dockerfile` for Postgres+pgvector, Redis, API,
      worker and a one-shot migrate service (written; **not executed** — see blocked)
- [x] Docs drafted: README, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`,
      `docs/EVALUATION.md` (EVALUATION reports NOT RUN, as the runner does not exist)

### Verification log (2026-10-20)
- `pytest tests -q` -> **318 passed**
- `pytest tests -q --cov=patchlens` -> **88%** statements (6210 statements, 595 missed;
  1552 branches, 267 partial)
- `ruff format --check patchlens tests` -> 91 files already formatted
- `ruff check patchlens tests` -> All checks passed
- `mypy patchlens` -> Success: no issues found in 72 source files
- `alembic upgrade head` / `alembic check` / `alembic downgrade base` on a fresh
  SQLite database -> all succeed
- `make demo-repo` -> indexed 5 files / 21 symbols / 21 embeddings
- `make demo-review` -> 12 findings, 4 verified, provider labelled `mock`
- Bugs found and fixed by this milestone's tests: evidence discriminator overwrite,
  snake_case tokenisation, prompt payload leaking unredacted secrets, LLM
  mode-rejection handling, overstated missing-test verification, body-size middleware
  raising instead of returning 413, app settings ignored by the auth dependency,
  trace missing rule metadata, `from __future__` imports flagged as unused,
  duplicate `query-inside-loop` findings per loop, truncated hunks on under-counted
  headers, AST checks analysing the base revision.

## Milestone 4 - Retrieval + evaluation
Acceptance: 5 comparable strategies; Recall@5, MRR@10, nDCG@10; precision/recall/F1
per category; latency/cost/failure-rate reporting; reproducible via scripts;
unrun metrics display NOT RUN.

- [x] BM25 (k1=1.5, b=0.75), semantic search, RRF fusion (k=60), heuristic reranker
- [x] Strategy-comparable retrieval interface + corpus cache
- [x] Evaluation datasets (retrieval + finding) with provenance and limitations
      (`backend/eval/datasets/retrieval-demo-v1.json`, `review-demo-v1.json`;
      content-hashed, immutable without a version bump, `must_find` labels,
      negative control, labels validated against the diff's added lines)
- [x] Metric implementations + runner + scripts + API endpoints
      (`services/evaluation/{datasets,metrics,runner}.py`; `eval-seed`, `eval-run`,
      `eval-report`; `GET/POST /api/v1/evaluation/...`; synchronous runs capped at
      200 cases; undefined metrics stored as `null`)
- [x] First real evaluation run recorded with numbers and failure analysis
      (retrieval `evr_022b326481384153bf7d1db625d47c66`: 12 queries × 5 strategies,
      0 failures, best `hybrid_rerank` nDCG@10 0.833 / MRR@10 0.938 / Recall@5 0.799;
      review `evr_205eb93f3fa5474f98dea8c70acfc7b9`: P 1.000 / R 0.929 / F1 0.963,
      must-find recall 0.857; the one miss is the case-003 logic bug, reported in
      `docs/EVALUATION.md`)

### Verification log (2026-10-20, milestone 4)
- `pytest tests -q` -> **354 passed**
- `pytest tests -q --cov=patchlens` -> **88%** statements (6855 statements, 646
  missed; 1710 branches, 288 partial)
- `ruff format --check patchlens tests` -> 101 files already formatted
- `ruff check patchlens tests` -> All checks passed
- `mypy patchlens` -> Success: no issues found in 77 source files
- Fresh SQLite: `alembic upgrade head` -> `alembic check` ("No new upgrade
  operations detected") -> `downgrade base` -> `upgrade head`: all succeed
  (migration `476ea5d76211` adds eval dataset `repository`/`commit_sha` provenance)
- `make demo-repo` -> 5 files / 21 symbols / 5 tests / 21 embeddings
- `make eval-seed && make eval-run` -> both datasets seeded; retrieval and review
  runs succeed with the numbers above; `make demo-review` -> 12 findings
  (4 verified, 8 need manual verification), provider `mock`
- Bugs found and fixed by this milestone's tests: static retrieval broke ties with
  random index-time symbol ids (the same corpus scored differently after a re-index);
  tests added in the same diff were invisible to test association (false
  "no related test" findings); `seed_dataset` did not flush case rows (autoflush is
  off); the CLI printed JSON twice; labels naming symbols absent from the corpus were
  scored silently instead of reported.


## Milestone 5 - GitHub integration
Acceptance: signed webhook verification, PR events, idempotent jobs, comments at
valid diff positions, least privilege, documented local demo path.

- [x] Webhook receiver with HMAC verification and delivery dedupe
      (`POST /api/v1/github/webhooks`, raw-body signature, `X-GitHub-Delivery`
      unique index, duplicate/no-op and payload-hash-conflict handling)
- [x] GitHub App JWT/installation tokens (RS256 nine-minute JWT, in-memory token
      cache refreshed before expiry, least-privilege permission set documented)
- [x] PR opened/reopened/synchronize handlers -> fetch diff -> review job
      (registered checkout + index required; `github-review:<repo>:<head_sha>` job
      dedupe; closed/draft/other actions audited as ignored)
- [x] Comment publication: only verified/needs-verification findings, valid anchors,
      bounded count, check-run summary (one `COMMENT` review, neutral check run,
      severity/anchor/limit filters, per-finding `publish_state`, redacted text, no
      raw excerpts)
- [x] Local demo workflow without GitHub App configuration (`make demo-*` works; a
      signed webhook ping can be sent locally as documented in `docs/GITHUB.md`)

### Verification log (2026-10-20, milestone 5)
- `pytest tests -q` -> **389 passed**
- `pytest tests -q --cov=patchlens` -> **88%** statements (7550 statements, 724
  missed; 1888 branches, 316 partial)
- `ruff format --check patchlens tests` -> 110 files already formatted
- `ruff check patchlens tests` -> All checks passed
- `mypy patchlens` -> Success: no issues found in 83 source files
- Fresh SQLite: `alembic upgrade head` -> `alembic check` ("No new upgrade
  operations detected") -> `downgrade base` -> `upgrade head`: all succeed
  (migration `9ed83ea31c6a` adds review-level publication state)
- `make init-db && make demo-repo && make demo-review` -> 5 files / 21 symbols /
  5 tests / 21 embeddings; 12 findings (4 verified, 8 need manual verification),
  provider `mock`; publication recorded as skipped because no GitHub publisher was
  configured
- End-to-end integration test: signed webhook -> queued job -> real worker -> real
  review pipeline -> mocked GitHub diff fetch -> one `COMMENT` review + neutral
  check run; findings persisted as published. The same suite covers wrong/missing
  signatures, malformed JSON, oversized bodies, duplicate deliveries, duplicate
  head-SHA jobs, draft/closed actions, unregistered repositories, installation
  add/remove/delete, auth on read endpoints and API failure marking findings failed.
- Bugs found and fixed by this milestone's tests: PyJWT rejects a non-string `iss`
  claim (GitHub accepts the string form); the pipeline did not attach the queue job id
  to the review it created; publication exception detail shadowed the skip reason;
  tests revealed the handler needed a real installation-token exchange in the mocked
  end-to-end path.
- **Not verified:** no live GitHub App installation, no request to github.com, no
  GitHub Enterprise, no remote cloning. Publishing is at-least-once across a crash
  between GitHub accepting a review and the DB commit; documented in
  `docs/GITHUB.md`.

## Milestone 6 - Test intelligence + isolation
Acceptance: related tests identified; candidate regression tests generated; execution
only in a restricted runner (or explicitly disabled); real outcomes recorded.

- [x] Structural related-test identification (consumed by the missing-test check)
- [ ] Candidate test generation from changed symbols + retrieval context
- [ ] Execution tiers: disabled (default), subprocess (explicit opt-in, documented as
      not a security boundary), Docker (network off, resource limits)
- [ ] Real execution results recorded; sandbox unavailability explained, never faked

## Milestone 7 - Reliability hardening
Acceptance: retries/timeouts/idempotency/concurrency limits exercised; degraded
modes documented; security review with tests.

- [x] Lease expiry/recovery, retry/backoff, cancellation and dedupe tests
- [x] Security tests: path traversal, auth, payload limits, prompt-injection handling,
      secret redaction, diff path rejection
- [ ] Idempotency and duplicate-event tests across API, queue and webhooks
- [ ] Timeout enforcement exercised end to end; failure-injection suite
- [ ] Documented load/soak evidence (or an explicit statement that none was gathered)

## Milestone 8 - Portfolio finish
Acceptance: README, architecture + data-flow diagrams, screenshots, CI, demo script,
resume bullets; every claim traceable to a command that was run.

- [ ] Frontend: Overview, Repositories, Reviews, Findings, Evaluation, Settings
      (real loading/error/empty states, keyboard accessible, no placeholder buttons)
- [~] README + ARCHITECTURE + SECURITY + EVALUATION docs written; recorded run
      numbers are in `docs/EVALUATION.md`. Screenshots and resume bullets still pending
- [x] Architecture and data-flow diagrams (Mermaid, `docs/ARCHITECTURE.md`)
- [x] docker-compose (Postgres+pgvector, Redis, migrate, API, worker) + `.env.example`
      (compose unexecuted, see blocked)
- [x] GitHub Actions CI (backend + migrations; frontend job to be added with the app)
- [x] Demo workflow producing a labelled mock review from a real fixture repo
- [ ] Screenshots captured from the running app
- [ ] Resume bullets with measured numbers

## Known environment limitations (kept honest in docs)
- Docker daemon was not running during development: Postgres+pgvector and Redis
  integration, plus the Docker test-execution tier, are implemented but have not
  been executed here. `docker compose up` and the sandbox tiers remain unverified.
- No live GitHub App installation or request to github.com: webhook handling, App
  auth, diff fetch, publication and the neutral check run are tested against a
  generated RSA key and an `httpx.MockTransport`. Remote cloning is not implemented,
  so webhook reviews require a registered local checkout and index.
- No paid LLM credentials were used; default runs use the deterministic mock
  provider and are labelled as such everywhere.
- The pgvector column is currently unconstrained (`vector` without a dimension), so
  the planned fixed-dimension column + HNSW index is a migration still to come; on
  SQLite the semantic path is an in-process scan.
- Re-indexing is required after changing `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` or
  `EMBEDDING_DIM`; cached vectors are keyed by all three.
