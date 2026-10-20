# Security model

PatchLens reads untrusted input — diffs, repository files, model output — and in
later milestones will execute generated tests. This document states what is enforced
today, what is planned, and where the gaps are. It describes the current code, not an
aspiration.

## Trust boundaries

| Input | Trust |
| --- | --- |
| Diff text (API, webhook) | untrusted, attacker-controlled |
| Repository file contents | untrusted; may contain prompt injection or secrets |
| LLM response | untrusted proposal; never authoritative |
| Generated test code (planned) | untrusted; must never run on the host |
| Environment configuration | trusted operator input |

## Implemented controls

### Authentication and authorization

- When `API_AUTH_TOKEN` is set, every data route (repositories, reviews, findings,
  jobs, settings, evaluation, GitHub installations/deliveries) requires
  `Authorization: Bearer <token>`. Comparison is constant-time
  (`hmac.compare_digest`).
- With no token configured the instance is explicitly a local development instance;
  this is why the frontend can discover `auth_required` from the public
  `/api/v1/meta` endpoint before it has a token.
- `/healthz`, `/readyz` and `/api/v1/meta` are public: probes must not need
  credentials, and meta exposes no secrets (provider names, capability flags,
  masked configuration).
- `POST /api/v1/github/webhooks` is public by necessity (GitHub cannot present a
  bearer token) and is authenticated by the HMAC signature instead. With no
  `GITHUB_WEBHOOK_SECRET` configured it returns 502 and never falls back to
  unauthenticated processing.
- Production refuses local checkout registration, so a deployment cannot be talked
  into indexing an arbitrary server path through the API.
- Request bodies are capped (`MAX_REQUEST_BYTES`, default 4 MiB; webhooks have their
  own smaller cap, `MAX_WEBHOOK_BYTES`, default 2 MiB, checked against the actual
  raw bytes). Oversized bodies get a structured 413 rather than a stack trace.

### GitHub webhook authenticity and replay protection

- The signature is computed over the **raw request body** and compared in constant
  time with `X-Hub-Signature-256`; missing, malformed or wrong signatures are 403 and
  no delivery row is written.
- `X-GitHub-Delivery` is unique in the database. A retry returns 202 `duplicate`
  without re-running work. If the same delivery id arrives with different body bytes
  (SHA-256 compared), the conflict is recorded in the response and logs rather than
  silently accepted.
- Unexpected processing errors roll back the delivery row, so a GitHub retry can be
  processed; deterministic failures (unregistered repository, malformed payload) are
  committed as `failed` with the reason so the dashboard is not silently empty.
- Supported events are allow-listed; unknown events are recorded as `ignored` rather
  than executed.

### GitHub App authentication and publication

- App authentication is RS256 JWT → installation token. Tokens are cached in memory
  only, refreshed before expiry, scoped to one installation, and never written to the
  database or logs.
- The App permission set is least-privilege: metadata read, pull requests write,
  checks write. The token cannot push code, change workflows, read secrets or
  administer the repository.
- Publication is filtered: only `verified` and `needs_manual_verification` findings,
  only configured severities, only verified added-line anchors and safe relative
  paths, and at most `GITHUB_MAX_COMMENTS_PER_REVIEW` comments.
- The review event is always `COMMENT`. The check run is always `neutral`; PatchLens
  never approves, requests changes or blocks a merge.
- Comment text excludes raw code excerpts and is passed through the same secret
  redaction as logs, so a detected hard-coded credential is not copied into a public
  pull-request comment.
- Every finding persists `publish_state`/`publish_detail`; API failure marks findings
  `failed` with a bounded provider message instead of claiming success.

### Path handling

- Every repository-relative path from a diff is normalised and checked before use:
  no absolute paths, drive letters, UNC prefixes, backslashes, `..` segments, NUL
  bytes or control characters.
- `resolve_within` resolves symlinks and verifies the final path is inside the
  checkout root, so a symlink cannot redirect a read outside the repository.
- Rejected paths are recorded in the review's failure map — an attempt is visible,
  not silently dropped.
- File reads are size-capped (`MAX_FILE_READ_BYTES`) before content is loaded.

### Diff and line validation

- Every finding must cite a line the diff added, on the new side. Removed lines and
  context lines cannot be used as anchors for new findings.
- Line ranges must be ordered and within the file; evidence excerpts must occur in
  the reviewed content. A finding that fails any check is stored as `rejected` with
  the reason, never published.

### Prompt injection

- Repository content is wrapped in labelled fences and the system prompt states that
  fenced content is data, never instructions.
- Secrets are redacted from the rendered prompt *and* from the machine-readable
  payload, using the same redaction pass.
- The model's output is a proposal: it must pass the same path/line/evidence
  validation as any other finding, so injected instructions cannot create a finding
  at an invented location.
- Prompt and response sizes are capped.

### Secrets

- `.env` is git-ignored; `.env.example` is generated from the settings model with
  empty secret fields and a portable SQLite path (a test enforces both).
- Secret-typed settings are `SecretStr`; the settings endpoint and CLI print masked
  values (`mask_secret`).
- The `hardcoded-secret` check redacts the detected literal before it is stored in a
  finding, so the review database does not become a secret store.
- Logging has a redaction pass for token-like values.

### Database

- All queries use SQLAlchemy expression language or bound parameters; no string-built
  SQL in application code.
- SQLite uses foreign keys with `ON DELETE CASCADE` where the model declares them;
  repositories can be deleted with their derived data.
- Statement timeouts are configurable for PostgreSQL.

## Planned and not yet implemented

These are milestone 6–7 items. They must not be described as protections until they
land with tests:

- **Test execution sandbox.** Execution tiers are planned: disabled by default,
  opt-in subprocess (explicitly *not* a security boundary), and Docker with network
  disabled and resource limits. Today no generated code is executed at all, and the
  API reports test execution as unavailable.
- **Outbound network policy** for the API/worker containers. Not implemented.

## Known limitations

- **No live GitHub App installation has been exercised.** Webhook verification,
  JWT/installation-token auth, diff fetch, publishing and the neutral check run are
  covered by integration tests with a generated RSA key and an `httpx.MockTransport`
  GitHub API. No request has been made to github.com from the development
  environment. See [GITHUB.md](GITHUB.md).
- **Publication is at-least-once across a crash.** GitHub has no idempotency key for
  review creation. If the process dies after GitHub accepts a review but before the
  database commit, a retry can create a second review. The stored review URL makes it
  visible; there is no reconciliation job.
- **No remote cloning.** A webhook for a repository without a registered local
  checkout and successful index is recorded as failed with that reason. The worker
  does not clone attacker-controlled repositories, which also removes an SSRF path.
- **GitHub Enterprise is untested.** `GITHUB_API_BASE_URL` is configurable, but only
  the mock base URL has been used.
- The mock LLM provider is not a security boundary; it exists to exercise the
  pipeline. Real model behaviour (including prompt injection resistance) can only be
  assessed with a real provider, which has not been run here.
- The heuristic reranker and hashing embeddings are not trained/neural; they do not
  affect security, but they do affect the quality of retrieved context.
- The public `/api/v1/meta` endpoint reveals provider mode and capability flags by
  design. If that is too much for a deployment, keep the service on a private
  network or put it behind an authenticating proxy.
- SQLite is a single-writer database; the worker enforces concurrency 1 there. This
  is a correctness measure, not a security control.

## Reporting

This is a portfolio project, not a production service. If you find an issue, open an
issue in the repository with a reproduction; do not include real credentials in the
report.
