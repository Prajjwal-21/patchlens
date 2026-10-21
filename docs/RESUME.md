# PatchLens — resume bullets

Every number below was measured on 2026-10-21 and is tied to a command or stored
run id in [TASKS.md](../TASKS.md) and [EVALUATION.md](EVALUATION.md). The default
provider is the deterministic mock; no paid LLM or neural embedding credentials were
used.

- **Built PatchLens**, a repository-aware AI code review platform (FastAPI,
  SQLAlchemy/Alembic, PostgreSQL 16 + pgvector, Redis, Next.js/TypeScript), with 18
  tables, a durable database-backed worker queue, hybrid BM25 + pgvector retrieval,
  GitHub App webhook publishing (tested against a mocked GitHub API), and a Next.js
  dashboard. **455 backend tests at 87% statement coverage**, 26 frontend tests, ruff
  and mypy strict clean; CI runs both stacks.

- **Implemented test intelligence and isolation**: generated candidate regression
  tests from changed symbols and retrieval context, with three explicit execution
  tiers (disabled by default, opt-in subprocess, Docker). The Docker runner uses
  `--network none`, read-only repository/test mounts, dropped capabilities, a
  read-only root filesystem and memory/CPU/PID limits; verified a generated candidate
  passing 3 tests in 545 ms with exit 0.

- **Built a reproducible evaluation harness** over versioned, content-hashed
  datasets. On PostgreSQL + pgvector, `hybrid_rerank` reached **nDCG@10 0.833 /
  MRR@10 0.938 / Recall@5 0.799** across 12 retrieval queries; a mock-provider review
  run reached **precision 1.000 / recall 0.929 / F1 0.963** with must-find recall
  0.857. Undefined metrics are stored and rendered as `UNKNOWN`, never zero.

- **Shipped the product surface and deployment**: a 19-route Next.js App Router
  dashboard with real loading/error/empty states and 12 captured screenshots; a
  five-service Docker Compose stack (PostgreSQL/pgvector, Redis, migrate, API,
  worker) with Redis wake-ups and healthchecks; and GitHub Actions CI for backend
  lint/type/test, migrations and frontend lint/typecheck/test/build.

## Short version

- Built a production-shaped AI code review platform with hybrid retrieval, durable
  jobs, GitHub App publishing and a Next.js dashboard; 455 backend tests, 87%
  coverage, 26 frontend tests.
- Added sandboxed test generation/execution and a reproducible eval harness; Docker
  runner verified with network disabled, hybrid retrieval nDCG@10 0.833, review F1
  0.963 on labelled fixtures.
- Containerized the full stack with PostgreSQL + pgvector, Redis and CI, and shipped
  12 dashboard screenshots with honest mock/`UNKNOWN` labelling.
