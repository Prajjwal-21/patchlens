# GitHub integration

PatchLens reviews pull requests from GitHub without a human in the request path. It
is deliberately a *reporter*: it comments, attaches a neutral check run, and never
approves, requests changes or blocks a merge.

This document describes what is implemented, the permissions the App needs, how to
configure it, and what has **not** been exercised.

## What is implemented

- `POST /api/v1/github/webhooks` verifies `X-Hub-Signature-256` (HMAC-SHA256 over
  the exact raw body) before parsing anything.
- Deliveries are deduplicated by `X-GitHub-Delivery` with a unique index. A retry
  is a 202 no-op and keeps the original outcome; a reused id with different content
  is recorded and reported as such.
- Supported events:
  - `pull_request` actions `opened`, `reopened`, `synchronize` → enqueue a review.
    `closed` updates the local pull-request state but does not review.
  - `installation` (`created`, `new_permissions_accepted`, `suspend`, `unsuspend`,
    `deleted`) → maintain the installation record; deleting clears repository links.
  - `installation_repositories` → update the stored repository count and links.
  - `ping` → acknowledged and audited.
  - Anything else is stored as `ignored` with the reason, not silently dropped.
- GitHub App authentication: RS256 app JWT (nine-minute lifetime) exchanged for an
  installation token, cached in memory and refreshed before expiry. Tokens are never
  written to the database.
- The worker fetches the pull-request diff with the installation token at execution
  time; the diff is not stored in the job payload.
- Review jobs are deduplicated by repository + head SHA, so redelivered webhooks and
  repeated `synchronize` events cannot run the same revision twice concurrently.
- Publication:
  - inline review comments for findings with verification status `verified` or
    `needs_manual_verification`;
  - only severities in `GITHUB_COMMENT_SEVERITIES`, capped by
    `GITHUB_MAX_COMMENTS_PER_REVIEW`;
  - anchors must be added lines on the new side (`side: RIGHT`) and a safe
    repository-relative path when `GITHUB_REQUIRE_VERIFIED_ANCHORS=true`;
  - one review with `event: COMMENT`, plus a **neutral** check run with annotations;
  - every finding records `publish_state` / `publish_detail`; a failed API call marks
    findings failed rather than claiming success;
  - comment text excludes raw code excerpts and is passed through secret redaction.
- Read endpoints for the dashboard:
  - `GET /api/v1/github/installations`
  - `GET /api/v1/github/deliveries` (optional `?status=processed|ignored|failed|duplicate|...`)

## Required GitHub App permissions

Use the minimum set. The App does not need code, secrets, actions, packages or
administration access.

| Permission | Access | Why |
| --- | --- | --- |
| Metadata | read | mandatory for every App |
| Pull requests | **write** | read the diff and create review comments |
| Checks | **write** | create the neutral check run and annotations |

Subscribe the App to the **Pull requests**, **Installation** and **Installation
repositories** events. If `Checks: write` is not granted, review comments still
publish; the check run is recorded as failed with the GitHub error.

## Configuration

```bash
GITHUB_APP_ID=123456
# PEM content with real newlines. In a .env file, quote it or use \n escapes.
GITHUB_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
GITHUB_WEBHOOK_SECRET=<random secret configured in the App>
GITHUB_PUBLISH_ENABLED=true          # publishing is off by default even with credentials
GITHUB_COMMENT_SEVERITIES=critical,high
GITHUB_MAX_COMMENTS_PER_REVIEW=10
GITHUB_REQUIRE_VERIFIED_ANCHORS=true
GITHUB_API_BASE_URL=https://api.github.com   # configurable for GitHub Enterprise
```

`GET /api/v1/settings` reports which pieces are configured, never their values.
Changing any of these requires restarting the API and worker.

## Setup

1. Create a GitHub App (Settings → Developer settings → GitHub Apps). Set the
   webhook URL to `https://<your-host>/api/v1/github/webhooks`, the secret, and the
   permissions/events above. Install it on the repositories to review.
2. Put the App id, private key and webhook secret in the environment. Set
   `GITHUB_PUBLISH_ENABLED=true` only when you intend comments to be created.
3. Clone and register the repository with a local checkout and index it. Remote
   cloning is **not** implemented, so the worker needs the checkout that was indexed:

   ```bash
   cd backend
   python -m patchlens.cli repo-add --owner <org> --name <repo> \
     --local-path /path/to/checkout
   python -m patchlens.cli index --repository-id <repo_id> --commit-sha <sha>
   ```

   The repository's `full_name` must match the GitHub `owner/name` (case-insensitive).
4. Open or synchronize a pull request. The delivery is acknowledged with 202; inspect
   `GET /api/v1/github/deliveries` and `GET /api/v1/jobs/{id}` for the outcome.

## Idempotency and failure semantics

| Layer | Guard |
| --- | --- |
| Webhook delivery | unique `X-GitHub-Delivery`; retries do not re-run |
| Job | active-job dedupe on `github-review:<repo>:<head_sha>` |
| Review | unique `(pull_request, commit_sha, strategy)` reuse |
| Publication | review-level `publish_state=published` short-circuit |
| Comment count | deterministic severity/order sort, then cap |

If the GitHub API fails while publishing, the review remains succeeded (the analysis
is valid) and the publication state is `failed` with a bounded error. A job-level
GitHub failure while **fetching** the diff is retryable and follows the queue's
backoff.

Known gap: GitHub has no idempotency key for review creation. If the process dies
after GitHub accepts the review but before the database commit, a retry can create a
second review. The stored review URL makes the duplicate visible; eliminating it
would require a reconciliation job, which is not implemented.

## Local demo without GitHub credentials

The GitHub path is not required to use PatchLens:

```bash
make demo-repo     # register + index examples/demo-repo
make demo-review   # run the review pipeline on the bundled pull request
```

That path exercises the real parser, retrieval, checks, validation and publisher
hook, but no GitHub API call. To exercise webhook verification locally, configure
`GITHUB_WEBHOOK_SECRET` and send a signed ping:

```bash
cd backend
python - <<'PY'
import json
import httpx
from patchlens.core.security import sign_github_payload

body = json.dumps({"zen": "local ping"}).encode()
headers = {
    "X-GitHub-Event": "ping",
    "X-GitHub-Delivery": "local-ping-1",
    "X-Hub-Signature-256": sign_github_payload(secret="your-secret", body=body),
}
print(httpx.post("http://localhost:8000/api/v1/github/webhooks", content=body, headers=headers).json())
PY
```

To review a pull request end to end you need a real App installation (or a local
fake GitHub API, as the integration test uses); PatchLens does not simulate GitHub
in production mode.

## What has not been exercised

- **No live GitHub App.** The client, webhook handler, dedupe, job and publisher are
  covered by integration tests with `httpx.MockTransport` and a generated RSA key;
  no request has been made to github.com from this environment.
- **No remote cloning.** A webhook for an unregistered repository, or one without a
  local checkout/index, is recorded as failed with that reason. It is not reviewed.
- **No GitHub Enterprise verification.** `GITHUB_API_BASE_URL` is configurable but
  only `https://api.github.test` (the mock) has been used.
- **No reconciliation of partial publication.** See the idempotency gap above.
- **No automatic indexing on synchronize.** The checkout must contain the indexed
  base revision; if the head commit changes the files the review needs, the pipeline
  reconstructs the new side from the diff, but the index remains at the base commit.

These limits are also surfaced in the API (`/api/v1/settings`, review metadata) so a
review is never presented as having GitHub context it did not have.
