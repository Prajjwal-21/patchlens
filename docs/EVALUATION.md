# Evaluation

PatchLens is judged on retrieval and review quality, not on how plausible its output
looks. This document defines the metrics, the dataset format, and the recorded runs
that back every number shown in the product.

## Anti-fabrication rules

1. A metric is reported only from a stored `eval_runs` row produced by the runner on a
   versioned dataset. There is no hand-entered number in the UI or docs.
2. Missing input is `NOT RUN`; an undefined ratio (for example recall with zero
   labelled items) is `UNKNOWN` with the reason. Neither is rendered as `0`.
3. Every run records: dataset id + version + content hash, corpus version
   (commit/content hash), strategy, embedding and LLM provider/model, k values,
   the PatchLens code revision, start/end timestamps and per-case results so an
   aggregate can be recomputed.
4. Cost is reported only when the provider returns token usage *and* explicit prices
   are configured. Otherwise cost is `UNKNOWN` — PatchLens does not guess prices.

## Datasets

Datasets live in `backend/eval/datasets/` as JSON. They are versioned and
content-hashed; seeding a changed document under an existing `name`/`version` fails
with "bump the version", so a recorded run can never silently start referring to
different labels.

### Retrieval dataset (`retrieval-demo-v1.json`)

```json
{
  "name": "retrieval-demo-v1",
  "version": "1",
  "kind": "retrieval",
  "repository": "demo/patchlens-demo",
  "commit_sha": "demo-local",
  "cases": [
    {
      "id": "ret-001",
      "query": "apply a percentage discount to a price",
      "relevant": [
        {"path": "app/calculator.py", "qualified_name": "apply_discount", "grade": 3},
        {"path": "app/api.py", "qualified_name": "handle_discount", "grade": 2},
        {"path": "app/pricing.py", "qualified_name": "PriceTable.lookup", "grade": 1}
      ],
      "notes": "grade 3 = directly answers; 2 = useful caller; 1 = tangential"
    }
  ]
}
```

Queries mix natural language, identifier lookup and test-oriented questions.
Graded relevance is required for nDCG; Recall@5 and MRR@10 treat `grade >= 1` as
relevant. Labels are validated against the corpus at run time: a label naming a
symbol that is not in the indexed version is reported, not silently ignored.

### Review dataset (`review-demo-v1.json`)

```json
{
  "name": "review-demo-v1",
  "version": "1",
  "kind": "review",
  "repository": "demo/patchlens-demo",
  "cases": [
    {
      "id": "review-001",
      "commit_sha": "demo-pr",
      "diff_text": "diff --git a/app/calculator.py ...",
      "expected": [
        {
          "category": "correctness",
          "rule_id": "bare-except",
          "path": "app/calculator.py",
          "line": 19,
          "must_find": true
        }
      ],
      "notes": "lines refer to the diff's new side"
    }
  ]
}
```

Every label must anchor to a line the diff *adds*; a label on a context or removed
line fails validation at load time, because a correct system can never produce it.
`must_find` marks labels whose absence is a hard failure (correctness and security
defects in the bundled dataset). Scoring weights all labels equally, and a separate
must-find recall prevents a critical miss from hiding behind optional labels.

A predicted finding matches an expected label when the **category is equal**, the
**path is equal** and the predicted line range **covers the expected line** (a range
is allowed because a reviewer may anchor a block at its first line). `rule_id` is
reported separately: finding the right problem with a different rule is still a find.

### Test-generation dataset (`test-generation-demo-v1.json`)

Each case contains a target function, its source and signature, related tests, and
one behaviour-changing mutation. Generation validity is always measured; execution,
pass rate and regression detection are measured only when the subprocess tier is
explicitly enabled. The dataset records that the mock generator produces an
importability smoke suite, so a default run measures validity, not behavioural test
quality.

## Metrics

### Retrieval

Computed per case, then macro-averaged over cases (each case weighted equally).

- **Recall@5** — `|relevant ∩ top5| / |relevant|`, using `grade >= 1`.
- **MRR@10** — `1 / rank` of the first relevant result in the top 10, or 0 if none.
- **nDCG@10** — `DCG@10 / IDCG@10` with `DCG = Σ (2^grade − 1) / log2(rank + 1)`.
- **Latency** — wall-clock per retrieval call, reported as p50 and p95 across cases.
- **Failure rate** — fraction of cases where retrieval raised; failed cases are
  excluded from the averages and reported in the failure count, never counted as 0.
- **Cost** — only when the embedding provider reports token usage and prices are
  configured; otherwise `UNKNOWN`.

Strategies compared: `static`, `bm25`, `semantic`, `hybrid`, `hybrid_rerank`, all on
the same corpus version and query set. A strategy that cannot run (for example
semantic without stored vectors) is recorded as a failure for that case; with zero
successful cases its metrics are `UNKNOWN`, not zero.

### Findings

- **Precision / recall / F1 per category** (`correctness`, `security`, `reliability`,
  `performance`, `maintainability`, `testing`, `style`) plus micro totals.
- **Must-find recall** — recall restricted to `must_find` labels; `UNKNOWN` when a
  dataset marks none.
- **Rule-match rate** — share of matched findings that used the labelled rule id;
  `UNKNOWN` when nothing matched.
- **Rejected count** — candidates the validation boundary refused (bad path, line
  not added, missing evidence, duplicate). Rejected candidates are not scored as
  findings; the count is reported so a broken extractor cannot look precise.
- **Verification mix** — counts of `verified`, `ai_suggested`,
  `needs_manual_verification`, `rejected` are visible per finding. They are
  descriptive, not a quality score.
- **Latency** — end-to-end review per case, p50 and p95.
- Runs do not include a human review of the predictions; the labels are the only
  ground truth, and that limitation is stored with every run.

### Test intelligence

- **Validity** — generated candidates that parse and are collected by pytest.
- **Execution rate** — candidates that actually ran in the sandbox tier, with the
  tier recorded.
- **Pass rate on the original revision** — executed candidates that pass.
- **Regression detection** — share of labelled mutation cases where a generated test
  fails on the mutated revision; reported as `UNKNOWN` when no mutations were
  evaluated.
- If no execution backend is available, these are `NOT RUN`/`UNKNOWN` with the
  reason, never fabricated. The harness currently uses the subprocess tier only and
  requires explicit opt-in; the Docker tier is used by the candidate-execution API,
  not by this harness.

## How runs are produced

```bash
# from the repository root, with the virtualenv active
make init-db          # or: cd backend && python -m alembic upgrade head
make demo-repo        # register + index examples/demo-repo at commit demo-local
make eval-seed        # load backend/eval/datasets/*.json
make eval-run         # retrieval-demo-v1 (all five strategies) + review-demo-v1

# or one at a time
cd backend
python -m patchlens.cli eval-seed --path eval/datasets
python -m patchlens.cli eval-run --dataset retrieval-demo-v1 --strategies bm25,semantic,hybrid
python -m patchlens.cli eval-run --dataset review-demo-v1
python -m patchlens.cli eval-report --run-id <run_id>       # add --json for machine output
```

Read path: `GET /api/v1/evaluation/datasets`, `GET /api/v1/evaluation/datasets/{id}`,
`GET /api/v1/evaluation/runs`, `GET /api/v1/evaluation/runs/{id}` and
`POST /api/v1/evaluation/runs` (synchronous, capped at 200 cases; longer runs belong
in the CLI). A dataset with no run is returned with `status="not_run"` and
`latest_run=null`; a run with an undefined metric returns `null`, which the UI
renders as `UNKNOWN`.

## Recorded runs — 2026-10-21 (PostgreSQL + pgvector)

These are the current runs. They use the Compose stack: PostgreSQL 16 with the
pgvector extension, Redis for wake-ups, hashing embeddings and the deterministic
mock provider.

| Component | Value |
| --- | --- |
| Database | PostgreSQL 16 + pgvector (Docker Compose) |
| Semantic backend | `pgvector` exact cosine-distance scan (`<=>`); no HNSW index |
| Embeddings | `hashing`, model `hashing-ngram-v2`, 512 dims, **non-neural** |
| LLM | `mock` provider (deterministic rule engine, `is_mock=true`) |
| Corpus | `demo/compose-demo` @ `compose-demo-v1`: 5 files, 21 symbols, 5 tests |
| Host | Docker Desktop Linux container (`Linux-...-linuxkit-aarch64`), Python 3.11.17 |
| PatchLens revision | working tree, version `0.1.0` |

The exact `GET /api/v1/evaluation/runs/{id}` serializer payloads are checked in:

- `docs/eval/2026-10-21-evr_1c874fcd28be4d27adb12542972fb79a.json`
- `docs/eval/2026-10-21-evr_a473310248634f82b8585d100bf75434.json`
- `docs/eval/2026-10-21-evr_35a3d64206234b39b3d50c03834bf1ad.json`

### Retrieval — run `evr_1c874fcd28be4d27adb12542972fb79a`

Dataset `retrieval-demo-v1` v1, content hash
`6208a5f5d6430b1093245ceb30248660bec0636d43bce5e9fb0d8bd5c35a2659`, 12 queries,
0 failures, 36 ms total. The dataset labels refer to `demo-local`; the indexed
version is `compose-demo-v1`. The run stores that mismatch as a limitation.

| Strategy | Recall@5 | MRR@10 | nDCG@10 | p50 ms | p95 ms | Failures |
| --- | --- | --- | --- | --- | --- | --- |
| static | 0.667 | 0.778 | 0.688 | 0.09 | 0.16 | 0 |
| bm25 | 0.778 | 0.771 | 0.756 | 0.18 | 0.26 | 0 |
| semantic | 0.750 | **1.000** | 0.809 | 0.41 | 0.88 | 0 |
| hybrid | **0.799** | 0.840 | 0.738 | 0.56 | 0.69 | 0 |
| hybrid_rerank | **0.799** | 0.938 | **0.833** | 0.68 | 0.81 | 0 |

`best_strategy` (highest nDCG@10): `hybrid_rerank`. Cost: `UNKNOWN`. The ordering is
close to the SQLite run but not identical (for example `semantic` nDCG@10 0.809 vs
0.789), which is expected from a different distance implementation and tie-breaking;
the best strategy and the main conclusions are unchanged. Latency is an in-process
retrieval measurement against a 21-symbol corpus, not a load test.

### Findings — run `evr_a473310248634f82b8585d100bf75434`

Dataset `review-demo-v1` v1, content hash
`0db0fc89c4da8622c45ff42e2a2d8ab75a2e2bad53327e8ef1fbff124da66370`, 3 diffs,
62 ms total. Provider: `mock` (rule engine), so this measures the deterministic
layer plus the mock's labelled heuristics — **not an LLM**.

| Metric | Value |
| --- | --- |
| Matches | tp 13, fp 0, fn 1 |
| Micro precision / recall / F1 | 1.000 / 0.929 / 0.963 |
| Must-find recall (7 labels) | 0.857 (6 found, 1 missed) |
| Rule-match rate (13 matches) | 1.000 |
| Rejected candidates | 0 |
| Latency p50 / p95 | 12.8 ms / 35.2 ms |

The per-case behaviour is the same as the 2026-10-20 SQLite run: `review-001` finds
all 12 labels, `review-002` (negative control) produces 0 findings, and
`review-003` misses the semantic off-by-one label while producing the missing-test
finding. Cost is `UNKNOWN`.

### Test generation — run `evr_35a3d64206234b39b3d50c03834bf1ad`

Dataset `test-generation-demo-v1` v1, content hash
`30c19903137e46fef425220b8932c7948565b093860f343e8d36aecc44df1231`, 3 targets,
2 ms total. Provider: `mock`; execution mode `disabled`.

| Metric | Value |
| --- | --- |
| Attempted / valid / rejected | 3 / 3 / 0 |
| Validity rate | 1.000 |
| Generation failure rate | 0.000 |
| Execution rate | `UNKNOWN` — sandbox disabled |
| Pass rate on original | `UNKNOWN` — sandbox disabled |
| Regression detection | `UNKNOWN` — no mutations evaluated |
| Latency p50 / p95 | 0.24 ms / 0.36 ms |

The mock generator emits importability smoke tests, so validity measures that the
candidates parse and are collectable; it does not measure behavioural test quality.
Execution was intentionally disabled for the evaluation run. The Docker tier was
verified separately through the candidate-execution API (candidate
`tst_437cecac3e1c40c6a7a383fbbb003025`: passed, exit 0, 3 tests, 545 ms).

## Recorded runs — 2026-10-20 (SQLite)

These are the original SQLite runs, kept for comparison. They were recorded before
the PostgreSQL/pgvector stack was exercised; the current runs are above. The
environment is part of the result, not an implementation detail:

| Component | Value |
| --- | --- |
| Database | SQLite (dev default); PostgreSQL/pgvector **not exercised** |
| Semantic backend | `portable_scan` (in-process exact cosine scan) |
| Embeddings | `hashing` provider, model `hashing-ngram-v2`, 512 dims, **non-neural** |
| LLM | `mock` provider (deterministic rule engine, `is_mock=true`) |
| Corpus | `demo/patchlens-demo` @ `demo-local`: 5 files, 21 symbols, 5 tests |
| Host | macOS, Python 3.11, single process, warm corpus cache |
| PatchLens revision | working tree (dirty; the run records `code_revision=unknown`) |

The full API payloads for the two 2026-10-20 runs are checked in as evidence:
`docs/eval/2026-10-20-retrieval-demo-v1.json` and
`docs/eval/2026-10-20-review-demo-v1.json`. They are the exact
`GET /api/v1/evaluation/runs/{id}` serializer output, including every per-case
prediction, and are not read by the application at runtime. The runs were recorded
in a temporary SQLite database, so re-running the commands creates new run ids with
the same numbers (subject to latency noise).

### Retrieval — run `evr_022b326481384153bf7d1db625d47c66`

Dataset `retrieval-demo-v1` v1, content hash
`6208a5f5d6430b1093245ceb30248660bec0636d43bce5e9fb0d8bd5c35a2659`, 12 queries,
0 failures, 39 ms total.

| Strategy | Recall@5 | MRR@10 | nDCG@10 | p50 ms | p95 ms | Failures |
| --- | --- | --- | --- | --- | --- | --- |
| static | 0.667 | 0.778 | 0.688 | 0.04 | 0.08 | 0 |
| bm25 | 0.778 | 0.771 | 0.756 | 0.12 | 0.21 | 0 |
| semantic | 0.750 | **1.000** | 0.789 | 0.68 | 0.74 | 0 |
| hybrid | **0.799** | 0.861 | 0.741 | 0.81 | 0.88 | 0 |
| hybrid_rerank | **0.799** | 0.938 | **0.833** | 0.92 | 1.00 | 0 |

`best_strategy` (highest nDCG@10): `hybrid_rerank`. Cost: `UNKNOWN` — the hashing
provider reports no token usage and no prices are configured.

What the numbers say, including the unflattering parts:

- The baseline wins on nothing, and returns **zero** candidates for
  `ret-009` ("HTTP handler for division"), because every query token is a
  sub-word that no identifier contains. That is the intended behaviour of an exact
  matcher, and it is why the baseline exists.
- `semantic` has the best MRR (1.000): with the hashing embedder every query's
  first hit is relevant. It still has lower Recall@5 than `hybrid`, so its
  ordering is better while its candidate set is narrower.
- `hybrid` improves Recall@5 over both components; the reranker then improves
  ordering (MRR 0.861 → 0.938, nDCG 0.741 → 0.833) without adding candidates.
- Latency is in-process retrieval only (SQLite, portable scan, warm cache). It is
  **not** an HTTP or pgvector measurement and must not be quoted as one.
- 21 symbols and 12 hand-written queries cannot establish production retrieval
  quality. The point of the table is that the harness, the numbers and the
  comparison are real and reproducible.

### Findings — run `evr_205eb93f3fa5474f98dea8c70acfc7b9`

Dataset `review-demo-v1` v1, content hash
`0db0fc89c4da8622c45ff42e2a2d8ab75a2e2bad53327e8ef1fbff124da66370`, 3 diffs,
49 ms total. Provider: `mock` (rule engine), so this measures the deterministic
layer plus the mock's labelled heuristics — **not** an LLM.

| Metric | Value |
| --- | --- |
| Matches | tp 13, fp 0, fn 1 |
| Micro precision / recall / F1 | 1.000 / 0.929 / 0.963 |
| Must-find recall (7 labels) | 0.857 (6 found, 1 missed) |
| Rule-match rate (13 matches) | 1.000 |
| Rejected candidates | 0 |
| Latency p50 / p95 | 10.9 ms / 27.9 ms |

| Category | Precision | Recall | F1 |
| --- | --- | --- | --- |
| correctness | 1.000 | 0.667 | 0.800 |
| security | 1.000 | 1.000 | 1.000 |
| performance | 1.000 | 1.000 | 1.000 |
| maintainability | 1.000 | 1.000 | 1.000 |
| testing | 1.000 | 1.000 | 1.000 |

Per case:

- `review-001` (demo PR, 12 labels): all 12 found, 0 false positives.
- `review-002` (negative control: a tested `PriceTable.total` addition): **0
  findings**. An earlier revision flagged it because the test added in the same diff
  was not yet indexed; the mapper now parses tests from the diff's new side, which
  removed those false positives. The regression is covered by an integration test.
- `review-003` (off-by-one in `mean()`): the missing-test finding is produced, but
  the correctness label is missed. This is the one `fn`, and it is deliberate: the
  case exists to show that no deterministic rule sees a semantic bug and the mock
  provider is not an LLM. The dataset is not tuned to make the score perfect.

Cost was not recorded for review runs: the mock provider reports no token usage and
no prices are configured (`UNKNOWN`, not an estimate).

## Reproducibility checklist

A recorded run is only meaningful with all of these captured:

- dataset id, version and content hash;
- repository version (commit SHA or content hash) and embedding provider/model/dim;
- retrieval strategy and k values; LLM provider and model for review runs;
- code revision of PatchLens itself;
- hardware/OS for latency numbers, and process warm/cold state;
- the exact command used.

All of the above are stored on the run row (`config`, `limitations`, `metrics`) and
returned by the API, so a report can be checked rather than trusted.

## Current status

| Area | Status |
| --- | --- |
| retrieval metrics + runner | RUN on SQLite and PostgreSQL/pgvector — current `evr_1c874fcd28be4d27adb12542972fb79a`, five strategies on 12 queries |
| finding precision/recall + must-find | RUN — current `evr_a473310248634f82b8585d100bf75434`, 3 diffs with the mock provider |
| test-generation validity | RUN — `evr_35a3d64206234b39b3d50c03834bf1ad`, 3/3 valid with the mock generator |
| test execution / regression detection | NOT RUN in evaluation — sandbox disabled; Docker tier verified separately through candidate execution |
| latency reporting | RUN for both retrieval backends and review; warm single-process measurements, not load tests |
| cost reporting | `UNKNOWN` by design: no priced provider was configured |
| real LLM or neural embeddings | NOT RUN — no paid credentials were used |
| independent review of the labels | NOT RUN — labels are author-written for this repository |
