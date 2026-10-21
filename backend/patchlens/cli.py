"""PatchLens command line interface.

Every command is a thin wrapper over the same services the API and worker use, so
the CLI can be used to reproduce anything the dashboard does - and nothing in the
CLI is a mock: ``index`` runs the real indexer, ``review`` runs the real pipeline
and reports which provider produced the findings.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from sqlalchemy import text

from patchlens import __version__
from patchlens.core.config import Settings, get_settings, reload_settings
from patchlens.core.enums import RetrievalStrategy
from patchlens.core.logging import configure_logging, get_logger, log_extra
from patchlens.db.models.evaluation import EvalCaseResult, EvalDataset, EvalRun
from patchlens.db.models.repository import Repository
from patchlens.db.session import create_all, get_database
from patchlens.jobs.queue import enqueue, job_to_dict
from patchlens.jobs.worker import run_worker
from patchlens.services.embedding.base import EmbeddingProvider
from patchlens.services.ingestion.indexer import RepositoryIndexer
from patchlens.services.providers.factory import build_embedding_provider
from patchlens.services.review.pipeline import ReviewPipeline, ReviewRequest

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="patchlens", description="PatchLens command line interface")
    parser.add_argument("--version", action="version", version=f"patchlens {__version__}")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--log-level", default=None, help="override LOG_LEVEL for this command")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the HTTP API (uvicorn)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes (development)")

    worker = subparsers.add_parser("worker", help="run the background job worker")
    worker.add_argument("--once", action="store_true", help="process at most one job, then exit")
    worker.add_argument("--max-jobs", type=int, default=None, help="stop after N jobs")
    worker.add_argument("--kinds", default=None, help="comma-separated job kinds to serve")
    worker.add_argument("--idle-cycles", type=int, default=None, help="stop after N empty polls")

    init = subparsers.add_parser("init-db", help="create tables (development) or print the alembic command")
    init.add_argument("--force", action="store_true", help="create tables even in production")

    index = subparsers.add_parser("index", help="index a local checkout")
    index.add_argument("--repository-id", required=True, help="repository id (repo_...)")
    index.add_argument("--commit-sha", default=None, help="commit sha to record (defaults to 'local')")
    index.add_argument("--branch", default=None)
    index.add_argument("--force", action="store_true", help="re-index even if the tree hash is unchanged")
    index.add_argument("--queue", action="store_true", help="enqueue the job instead of running it inline")

    repo_add = subparsers.add_parser("repo-add", help="register a local checkout (development/demo)")
    repo_add.add_argument("--owner", required=True)
    repo_add.add_argument("--name", required=True)
    repo_add.add_argument("--local-path", required=True, help="absolute path to the checkout")
    repo_add.add_argument("--default-branch", default="main")

    repo_list = subparsers.add_parser("repo-list", help="list registered repositories")
    repo_list.add_argument("--limit", type=int, default=50)

    review = subparsers.add_parser("review", help="review a unified diff")
    review.add_argument("--repository-id", required=True)
    review.add_argument("--diff-file", required=True, help="path to a unified diff (use - for stdin)")
    review.add_argument("--commit-sha", default="local-head")
    review.add_argument("--base-sha", default=None)
    review.add_argument(
        "--include-rejected", action="store_true", help="print rejected/deduplicated findings too"
    )
    review.add_argument("--queue", action="store_true", help="enqueue the job instead of running it inline")
    review.add_argument(
        "--generate-tests",
        action="store_true",
        help="enable candidate generation for this review (overrides TEST_GENERATION_ENABLED)",
    )

    subparsers.add_parser("test-status", help="show the effective test-execution sandbox and limits")
    test_candidates = subparsers.add_parser("test-candidates", help="list generated test candidates")
    test_candidates.add_argument("--review-id", default=None)
    test_candidates.add_argument("--repository-id", default=None)
    test_candidates.add_argument("--status", default=None)
    test_candidates.add_argument("--limit", type=int, default=50)

    test_generate = subparsers.add_parser(
        "test-generate", help="generate candidates for symbols in the latest indexed snapshot"
    )
    test_generate.add_argument("--repository-id", required=True)
    test_generate.add_argument(
        "--target",
        action="append",
        required=True,
        help="path::qualified_name; repeat for multiple targets",
    )

    test_execute = subparsers.add_parser(
        "test-execute", help="execute one candidate in the configured sandbox"
    )
    test_execute.add_argument("--candidate-id", required=True)

    jobs = subparsers.add_parser("jobs", help="list recent jobs")
    jobs.add_argument("--limit", type=int, default=20)
    jobs.add_argument("--status", default=None)

    config = subparsers.add_parser("config", help="print effective (redacted) configuration")
    config.add_argument("--write-env-example", action="store_true", help="emit a .env.example to stdout")

    eval_seed = subparsers.add_parser("eval-seed", help="load evaluation dataset JSON into the database")
    eval_seed.add_argument("--path", required=True, help="dataset file, or a directory of *.json datasets")

    eval_run = subparsers.add_parser("eval-run", help="run an evaluation dataset and record the result")
    eval_run.add_argument("--dataset", required=True, help="dataset name (latest version is used)")
    eval_run.add_argument(
        "--repository-id", default=None, help="override the repository named by the dataset"
    )
    eval_run.add_argument(
        "--strategies",
        default=None,
        help="comma-separated retrieval strategies (default: all five)",
    )

    eval_report = subparsers.add_parser("eval-report", help="print a recorded evaluation run")
    eval_report.add_argument("--run-id", default=None, help="run id (default: the latest run)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.log_level:
        reload_settings()
    settings = get_settings()
    configure_logging(level=args.log_level or settings.log_level)

    handlers = {
        "serve": _cmd_serve,
        "worker": _cmd_worker,
        "init-db": _cmd_init_db,
        "repo-add": _cmd_repo_add,
        "repo-list": _cmd_repo_list,
        "index": _cmd_index,
        "review": _cmd_review,
        "test-status": _cmd_test_status,
        "test-candidates": _cmd_test_candidates,
        "test-generate": _cmd_test_generate,
        "test-execute": _cmd_test_execute,
        "jobs": _cmd_jobs,
        "config": _cmd_config,
        "eval-seed": _cmd_eval_seed,
        "eval-run": _cmd_eval_run,
        "eval-report": _cmd_eval_report,
    }
    try:
        return handlers[args.command](args, settings)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def _emit(args: argparse.Namespace, payload: Any, *, human: str | None = None) -> None:
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(human if human is not None else json.dumps(payload, indent=2, default=str))


def _cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    host = args.host
    if settings.is_production and host in {"0.0.0.0", "::"} and settings.api_auth_token is None:  # noqa: S104
        print(
            "refusing to bind to a public interface in production without API_AUTH_TOKEN",
            file=sys.stderr,
        )
        return 2
    print(f"PatchLens API on http://{host}:{args.port} (environment={settings.environment})")
    uvicorn.run(
        "patchlens.api.app:create_app",
        factory=True,
        host=host,
        port=args.port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
    )
    return 0


def _cmd_worker(args: argparse.Namespace, settings: Settings) -> int:
    kinds = [item.strip() for item in args.kinds.split(",") if item.strip()] if args.kinds else None
    executed = run_worker(
        settings=settings,
        kinds=kinds,
        max_jobs=1 if args.once else args.max_jobs,
        idle_cycles=1 if args.once else args.idle_cycles,
    )
    _emit(args, {"executed": executed}, human=f"processed {executed} job(s)")
    return 0


def _cmd_init_db(args: argparse.Namespace, settings: Settings) -> int:
    if settings.is_production and not args.force:
        print(
            "production schema is managed by alembic. Run: alembic upgrade head\n"
            "(use --force to create missing tables anyway)",
            file=sys.stderr,
        )
        return 2
    database = get_database()
    create_all(database)
    rows = [
        {"tool": "create_all", "dialect": database.dialect, "database": _redact_dsn(settings.database_url)}
    ]
    _emit(args, {"created": rows}, human=f"schema ready on {database.dialect}")
    return 0


def _cmd_repo_add(args: argparse.Namespace, settings: Settings) -> int:
    """Register a local checkout so index/review commands can use it."""
    from sqlalchemy import select

    if settings.is_production:
        print(
            "registering local checkouts is disabled in production; install the GitHub App instead",
            file=sys.stderr,
        )
        return 2
    root = Path(args.local_path).expanduser()
    if not root.is_dir():
        print(f"local path does not exist or is not a directory: {root}", file=sys.stderr)
        return 1
    full_name = Repository.make_full_name(args.owner, args.name)
    database = get_database()
    with database.session() as session:
        existing = session.scalar(select(Repository).where(Repository.full_name == full_name))
        if existing is not None:
            _emit(
                args,
                {"id": existing.id, "full_name": existing.full_name, "created": False},
                human=f"already registered: {existing.id} ({existing.full_name})",
            )
            return 0
        repository = Repository(
            provider="local",
            owner=args.owner,
            name=args.name,
            full_name=full_name,
            default_branch=args.default_branch,
            local_path=str(root.resolve()),
        )
        session.add(repository)
        session.flush()
        payload = {"id": repository.id, "full_name": repository.full_name, "created": True}
    _emit(args, payload, human=f"registered {payload['full_name']} as {payload['id']}")
    return 0


def _cmd_repo_list(args: argparse.Namespace, settings: Settings) -> int:
    """List registered repositories with the ids the other commands take."""
    from sqlalchemy import select

    database = _read_session_or_hint(settings)
    with database.session() as session:
        repositories = list(
            session.scalars(select(Repository).order_by(Repository.created_at).limit(args.limit))
        )
        payload = [
            {
                "id": repository.id,
                "full_name": repository.full_name,
                "provider": repository.provider,
                "index_status": repository.index_status,
                "indexed_commit_sha": repository.indexed_commit_sha,
                "local_path": repository.local_path,
            }
            for repository in repositories
        ]
    _emit(
        args,
        {"repositories": payload},
        human="\n".join(
            f"{item['id']} {item['full_name']:<32} {item['index_status']:<9} "
            f"commit={item['indexed_commit_sha'] or '-'}"
            for item in payload
        )
        or "no repositories registered",
    )
    return 0


def _cmd_index(args: argparse.Namespace, settings: Settings) -> int:
    database = get_database()
    with database.session() as session:
        repository = session.get(Repository, args.repository_id)
        if repository is None:
            print(f"repository {args.repository_id} not found", file=sys.stderr)
            return 1
        if args.queue:
            result = enqueue(
                session,
                kind="index_repository",
                payload={
                    "repository_id": repository.id,
                    "commit_sha": args.commit_sha or repository.indexed_commit_sha or "local",
                    "branch": args.branch,
                    "force": args.force,
                },
                dedupe_key=f"index:{repository.id}:{args.commit_sha or 'local'}",
                settings=settings,
            )
            session.commit()
            _emit(args, job_to_dict(result.job, include_events=True), human=f"queued job {result.job.id}")
            return 0
        if not repository.local_path:
            print("repository has no local checkout configured", file=sys.stderr)
            return 1
        outcome = RepositoryIndexer(
            settings=settings, embedding_provider=_embedding_provider(settings)
        ).index(
            session,
            repository=repository,
            root=Path(repository.local_path),
            commit_sha=args.commit_sha or "local",
            branch=args.branch,
            force=args.force,
        )
        session.commit()
        payload = outcome.to_dict()
    human = (
        f"indexed {payload['files']} files, {payload['symbols']} symbols, {payload['tests']} tests, "
        f"{payload['embeddings']} embeddings in {payload['duration_ms']} ms"
        + (" (up to date)" if payload.get("up_to_date") else "")
    )
    _emit(args, payload, human=human)
    return 0


def _cmd_review(args: argparse.Namespace, settings: Settings) -> int:
    if args.generate_tests:
        settings = settings.model_copy(update={"test_generation_enabled": True})
    if args.diff_file == "-":
        diff_text = sys.stdin.read()
    else:
        diff_text = Path(args.diff_file).read_text(encoding="utf-8")
    database = get_database()
    with database.session() as session:
        repository = session.get(Repository, args.repository_id)
        if repository is None:
            print(f"repository {args.repository_id} not found", file=sys.stderr)
            return 1
        if args.queue:
            result = enqueue(
                session,
                kind="review_pull_request",
                payload={
                    "repository_id": repository.id,
                    "diff_text": diff_text,
                    "commit_sha": args.commit_sha,
                    "base_sha": args.base_sha,
                    "trigger": "cli",
                },
                dedupe_key=f"review:{repository.id}:{args.commit_sha}",
                settings=settings,
            )
            session.commit()
            _emit(args, job_to_dict(result.job, include_events=True), human=f"queued job {result.job.id}")
            return 0

        run = ReviewPipeline(session, settings=settings).run(
            ReviewRequest(
                repository=repository,
                diff_text=diff_text,
                commit_sha=args.commit_sha,
                base_sha=args.base_sha,
                trigger="cli",
            )
        )
        session.commit()
        review = run.review
        findings = [
            {
                "severity": finding.severity,
                "verification_status": finding.verification_status,
                "rule_id": finding.rule_id,
                "detector": finding.detector,
                "path": finding.path,
                "start_line": finding.start_line,
                "line_verified": finding.line_verified,
                "title": finding.title,
                "suggestion": finding.suggestion,
                "rejection_reason": finding.rejection_reason,
            }
            for finding in run.findings
            if args.include_rejected or finding.verification_status != "rejected"
        ]
        warnings = list(run.warnings)
        payload = {
            "review_id": review.id,
            "status": review.status,
            "stage": review.stage,
            "provider": review.llm_provider,
            "model": review.llm_model,
            "is_mock_provider": review.is_mock_provider,
            "findings": review.finding_count,
            "verified_findings": review.verified_finding_count,
            "warnings": warnings,
            "error": review.error,
            "candidate_tests": review.candidate_test_count,
            "test_generation": (review.metadata_json or {}).get("test_generation"),
            "items": findings,
        }
    lines = [
        f"review {payload['review_id']} {payload['status']} "
        f"(provider={payload['provider']}{' MOCK' if payload['is_mock_provider'] else ''})",
        f"findings: {payload['findings']} (verified: {payload['verified_findings']})",
        f"candidate tests: {payload['candidate_tests']}",
    ]
    for item in findings:
        marker = "!" if item["verification_status"] == "verified" else "?"
        lines.append(
            f"  {marker} [{item['severity']}] {item['verification_status']:<24} {item['rule_id']:<34} "
            f"{item['path']}:{item['start_line']} {item['title']}"
        )
    generation = cast(dict[str, Any], payload.get("test_generation") or {})
    if generation:
        lines.append(
            f"test generation: {generation.get('state')} "
            f"(generated={generation.get('generated')}, rejected={generation.get('rejected')})"
        )
    if warnings:
        lines.append("warnings: " + "; ".join(warnings))
    if payload["error"]:
        lines.append(f"error: {payload['error']}")
    _emit(args, payload, human="\n".join(lines))
    return 0 if payload["status"] == "succeeded" else 1


def _cmd_test_status(args: argparse.Namespace, settings: Settings) -> int:
    from patchlens.services.testing.execution import resolve_sandbox

    status = resolve_sandbox(settings)
    _emit(
        args,
        status.to_dict(),
        human=(
            f"execution: available={status.available} mode={status.effective_mode}\n"
            f"reason: {status.reason or '-'}\n"
            f"detail: {status.detail}\n"
            f"limits: timeout={status.limits['timeout_seconds']}s "
            f"memory={status.limits['memory_mb']}MB network={status.limits['network_enabled']}"
        ),
    )
    return 0


def _cmd_test_candidates(args: argparse.Namespace, settings: Settings) -> int:
    from sqlalchemy import select

    from patchlens.db.models.testing import TestCandidate

    database = _read_session_or_hint(settings)
    with database.read_session() as session:
        statement = select(TestCandidate).order_by(TestCandidate.created_at.desc()).limit(args.limit)
        if args.review_id:
            statement = statement.where(TestCandidate.review_id == args.review_id)
        if args.repository_id:
            statement = statement.where(TestCandidate.repository_id == args.repository_id)
        if args.status:
            statement = statement.where(TestCandidate.status == args.status)
        rows = list(session.scalars(statement))
        payload = [
            {
                "id": row.id,
                "review_id": row.review_id,
                "repository_id": row.repository_id,
                "target": f"{row.target_path}::{row.target_symbol}",
                "test_path": row.suggested_test_path,
                "status": row.status,
                "provider": row.provider,
                "model": row.model,
                "is_mock_provider": row.is_mock_provider,
                "executed_at": row.executed_at,
                "exit_code": row.exit_code,
                "execution_disabled_reason": row.execution_disabled_reason,
            }
            for row in rows
        ]
    _emit(
        args,
        {"candidates": payload},
        human="\n".join(
            f"{item['id']} {item['status']:<20} {item['target']:<40} -> {item['test_path']} "
            f"({'MOCK' if item['is_mock_provider'] else item['provider']})"
            for item in payload
        )
        or "no test candidates",
    )
    return 0


def _cmd_test_generate(args: argparse.Namespace, settings: Settings) -> int:
    from patchlens.services.providers.factory import build_llm_provider
    from patchlens.services.testing.generator import generate_candidates, targets_from_indexed

    requested: list[tuple[str, str]] = []
    for item in args.target:
        path, separator, symbol = item.partition("::")
        if not separator or not path or not symbol:
            print(f"invalid --target {item!r}; expected path::qualified_name", file=sys.stderr)
            return 2
        requested.append((path, symbol))
    database = get_database()
    with database.session() as session:
        repository = session.get(Repository, args.repository_id)
        if repository is None:
            print(f"repository {args.repository_id} not found", file=sys.stderr)
            return 1
        effective = settings.model_copy(update={"test_generation_enabled": True})
        targets = targets_from_indexed(
            session,
            repository=repository,
            requested=requested,
            limit=effective.test_generation_max_candidates,
        )
        if not targets:
            print(
                "no requested symbol with source was found in the latest indexed snapshot; "
                "index the repository first",
                file=sys.stderr,
            )
            return 1
        provider = build_llm_provider(effective)
        try:
            generated = generate_candidates(
                session,
                repository=repository,
                targets=targets,
                settings=effective,
                llm_provider=provider,
                commit_sha=repository.indexed_commit_sha,
            )
        finally:
            provider.close()
        session.commit()
        candidates = [
            {
                "id": candidate.id,
                "target": f"{candidate.target_path}::{candidate.target_symbol}",
                "test_path": candidate.suggested_test_path,
                "status": candidate.status,
                "provider": candidate.provider,
                "is_mock_provider": candidate.is_mock_provider,
                "execution_disabled_reason": candidate.execution_disabled_reason,
            }
            for candidate in generated.candidates
        ]
        payload = {
            "source": "indexed_snapshot",
            "generated": len(candidates),
            "rejected": generated.rejected,
            "candidates": candidates,
            "note": (
                "generation used the latest indexed snapshot, not a pull-request head; "
                "generation is not execution"
            ),
        }
    _emit(
        args,
        payload,
        human="\n".join(
            [
                f"generated {len(candidates)} candidate(s), rejected {len(generated.rejected)}",
                *[
                    f"  {item['id']} {item['status']:<20} {item['target']} -> {item['test_path']}"
                    for item in candidates
                ],
            ]
        ),
    )
    return 0


def _cmd_test_execute(args: argparse.Namespace, settings: Settings) -> int:
    from patchlens.db.models.testing import TestCandidate
    from patchlens.services.testing.execution import (
        execute_candidate,
        mark_execution_disabled,
        resolve_sandbox,
    )

    database = get_database()
    with database.session() as session:
        candidate = session.get(TestCandidate, args.candidate_id)
        if candidate is None:
            print(f"test candidate {args.candidate_id} not found", file=sys.stderr)
            return 1
        sandbox = resolve_sandbox(settings)
        if not sandbox.available:
            outcome = mark_execution_disabled(session, candidate, sandbox)
            session.commit()
            payload = {"candidate_id": candidate.id, **outcome.to_dict()}
            _emit(args, payload, human=f"not executed: {sandbox.reason}")
            return 1
        outcome = execute_candidate(session, candidate, settings)
        session.commit()
        payload = {
            "candidate_id": candidate.id,
            "status": candidate.status,
            "executed": outcome.executed,
            "exit_code": candidate.exit_code,
            "duration_ms": candidate.duration_ms,
            "timed_out": candidate.timed_out,
            "stdout_tail": candidate.stdout_tail,
            "stderr_tail": candidate.stderr_tail,
        }
    _emit(
        args,
        payload,
        human=(
            f"{payload['status']} exit={payload['exit_code']} duration={payload['duration_ms']}ms\n"
            f"stdout:\n{payload['stdout_tail'] or ''}\nstderr:\n{payload['stderr_tail'] or ''}"
        ),
    )
    return 0 if payload["status"] == "passed" else 1


def _read_session_or_hint(settings: Settings) -> Any:
    """A read session, or exit with a fixable message when the schema is missing.

    A fresh checkout that has never run ``init-db`` must not see a raw SQLAlchemy
    traceback; it must be told the one command that fixes it.
    """
    from sqlalchemy.exc import OperationalError

    database = get_database()
    try:
        with database.read_session() as session:
            session.execute(text("SELECT 1 FROM repositories LIMIT 1"))
    except OperationalError:
        target = "alembic upgrade head" if settings.is_production else "python -m patchlens.cli init-db"
        print(
            f"the database at {_redact_dsn(settings.database_url)} has no schema yet; run: {target}",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    return database


def _cmd_jobs(args: argparse.Namespace, settings: Settings) -> int:
    from sqlalchemy import select

    from patchlens.db.models.job import Job

    database = _read_session_or_hint(settings)
    with database.read_session() as session:
        statement = select(Job).order_by(Job.created_at.desc()).limit(args.limit)
        if args.status:
            statement = statement.where(Job.status == args.status)
        jobs = [job_to_dict(job) for job in session.scalars(statement)]
    _emit(
        args,
        {"jobs": jobs},
        human="\n".join(
            f"{job['id']} {job['kind']:<22} {job['status']:<10} attempts={job['attempts']} "
            f"stage={job['stage']} error={job['error'] or '-'}"
            for job in jobs
        )
        or "no jobs",
    )
    return 0


def _cmd_eval_seed(args: argparse.Namespace, settings: Settings) -> int:
    """Load dataset files, refusing to replace labels under an existing version."""
    from sqlalchemy import select

    from patchlens.services.evaluation.datasets import (
        content_hash,
        load_dataset,
        seed_dataset,
    )

    path = Path(args.path)
    if path.is_dir():
        files = sorted(item for item in path.glob("*.json") if item.is_file())
    elif path.is_file():
        files = [path]
    else:
        print(f"dataset path does not exist: {path}", file=sys.stderr)
        return 1
    if not files:
        print(f"no dataset JSON files under {path}", file=sys.stderr)
        return 1

    database = get_database()
    rows: list[dict[str, Any]] = []
    with database.session() as session:
        for file in files:
            try:
                dataset = load_dataset(file)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            digest = content_hash(dataset)
            existing = session.scalar(
                select(EvalDataset).where(
                    EvalDataset.name == dataset.name, EvalDataset.version == dataset.version
                )
            )
            if existing is not None and existing.content_hash == digest:
                rows.append(
                    {
                        "id": existing.id,
                        "name": existing.name,
                        "version": existing.version,
                        "kind": existing.kind,
                        "cases": existing.case_count,
                        "state": "unchanged",
                    }
                )
                continue
            try:
                row = seed_dataset(session, dataset, source_path=file)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            rows.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "version": row.version,
                    "kind": row.kind,
                    "cases": row.case_count,
                    "state": "seeded",
                }
            )
        session.commit()
    human = "\n".join(
        f"{item['state']:<9} {item['name']} v{item['version']} ({item['kind']}, {item['cases']} cases)"
        for item in rows
    )
    _emit(args, {"datasets": rows}, human=human)
    return 0


def _cmd_eval_run(args: argparse.Namespace, settings: Settings) -> int:
    """Run a dataset and print the aggregate that was stored."""
    from sqlalchemy import select

    from patchlens.services.evaluation.datasets import load_cases
    from patchlens.services.evaluation.runner import (
        run_retrieval_evaluation,
        run_review_evaluation,
        run_test_generation_evaluation,
    )

    database = get_database()
    with database.session() as session:
        dataset = session.scalar(
            select(EvalDataset)
            .where(EvalDataset.name == args.dataset)
            .order_by(EvalDataset.created_at.desc())
        )
        if dataset is None:
            names = [row.name for row in session.scalars(select(EvalDataset)).all()]
            print(
                f"dataset {args.dataset!r} is not seeded"
                + (f"; available: {', '.join(sorted(names))}" if names else "; run eval-seed first"),
                file=sys.stderr,
            )
            return 1
        if args.repository_id:
            repository = session.get(Repository, args.repository_id)
            if repository is None:
                print(f"repository {args.repository_id} not found", file=sys.stderr)
                return 1
        else:
            repository = session.scalar(select(Repository).where(Repository.full_name == dataset.repository))
            if repository is None:
                print(
                    f"dataset refers to {dataset.repository!r}, which is not registered; "
                    "index it or pass --repository-id",
                    file=sys.stderr,
                )
                return 1
        cases = load_cases(session, dataset)
        if not cases:
            print(f"dataset {dataset.name!r} has no cases", file=sys.stderr)
            return 1
        if dataset.kind == "retrieval":
            if args.strategies:
                requested = [item.strip() for item in args.strategies.split(",") if item.strip()]
                allowed = {strategy.value for strategy in RetrievalStrategy}
                unknown = sorted(set(requested) - allowed)
                if unknown:
                    print(
                        f"unknown strategy {', '.join(unknown)}; choose from {', '.join(sorted(allowed))}",
                        file=sys.stderr,
                    )
                    return 1
                strategies = [RetrievalStrategy(name) for name in requested]
            else:
                strategies = list(RetrievalStrategy)
            run = run_retrieval_evaluation(
                session,
                dataset=dataset,
                cases=cases,
                repository=repository,
                settings=settings,
                strategies=strategies,
                dataset_commit_sha=dataset.commit_sha,
            )
        elif dataset.kind == "test_generation":
            run = run_test_generation_evaluation(
                session,
                dataset=dataset,
                cases=cases,
                repository=repository,
                settings=settings,
            )
        else:
            run = run_review_evaluation(
                session,
                dataset=dataset,
                cases=cases,
                repository=repository,
                settings=settings,
            )
        session.commit()
        results = list(session.scalars(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id)).all())
        payload = _eval_run_payload(run, dataset, results)
    human = _eval_run_human(payload)
    _emit(args, payload, human=human)
    return 0 if run.status == "succeeded" else 1


def _eval_run_payload(run: EvalRun, dataset: EvalDataset, results: list[EvalCaseResult]) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "dataset": {"id": dataset.id, "name": dataset.name, "version": dataset.version, "kind": dataset.kind},
        "status": run.status,
        "sample_count": run.sample_count,
        "metrics": run.metrics or {},
        "strategy_metrics": run.strategy_metrics or {},
        "config": run.config or {},
        "limitations": run.limitations or [],
        "notes": run.notes,
        "error": run.error,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_ms": run.duration_ms,
        "cases": [
            {
                "case_key": result.case_key,
                "strategy": result.strategy,
                "predictions": len(result.predictions),
                "latency_ms": result.latency_ms,
                "error": result.error,
                "metrics": {
                    key: value
                    for key, value in (result.metrics or {}).items()
                    if key
                    in {
                        "precision",
                        "recall",
                        "f1",
                        "expected_count",
                        "rejected_count",
                        "must_find_total",
                        "must_find_missed",
                        "unexpected",
                        "missed",
                        "validity_rate",
                        "execution_rate",
                        "executed",
                        "pass_rate_on_original",
                        "regression_detection_rate",
                        "generation_failure_rate",
                        "mutations",
                    }
                    or key.startswith(("recall@", "mrr@", "ndcg@"))
                },
            }
            for result in results
        ],
    }


def _eval_run_human(payload: dict[str, Any]) -> str:
    dataset = payload["dataset"]
    lines = [
        f"run {payload['run_id']} {payload['status']} "
        f"({dataset['name']} v{dataset['version']}, {dataset['kind']}, {payload['sample_count']} cases)"
    ]
    if payload["error"]:
        lines.append(f"error: {payload['error']}")
    if payload["strategy_metrics"]:
        for name, metrics in payload["strategy_metrics"].items():
            recall = metrics.get("recall@5")
            lines.append(
                f"  {name:<14} recall@5={_fmt(recall)} mrr@10={_fmt(metrics.get('mrr@10'))} "
                f"ndcg@10={_fmt(metrics.get('ndcg@10'))} failures={metrics.get('failures')}"
            )
        best = (payload["metrics"] or {}).get("best_strategy")
        if best:
            lines.append(f"  best strategy: {best}")
    overall = (payload["metrics"] or {}).get("overall")
    if dataset["kind"] == "test_generation":
        metrics = payload["metrics"] or {}
        lines.append(
            f"  validity={_fmt(metrics.get('validity_rate'))} "
            f"execution={_fmt(metrics.get('execution_rate'))} "
            f"pass_on_original={_fmt(metrics.get('pass_rate_on_original'))} "
            f"regression_detection={_fmt(metrics.get('regression_detection_rate'))}"
        )
        unknowns = metrics.get("unknowns") or []
        if unknowns:
            lines.append("  UNKNOWN: " + "; ".join(unknowns))
    if overall:
        lines.append(
            f"  overall precision={_fmt(overall.get('precision'))} "
            f"recall={_fmt(overall.get('recall'))} f1={_fmt(overall.get('f1'))} "
            f"(tp={overall.get('tp')} fp={overall.get('fp')} fn={overall.get('fn')})"
        )
        missed = (payload["metrics"] or {}).get("missed_expected") or []
        if missed:
            lines.append(f"  missed expected: {', '.join(missed)}")
    if payload["notes"]:
        lines.append(f"notes: {payload['notes']}")
    for limitation in payload["limitations"]:
        lines.append(f"limitation: {limitation}")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    """Format a metric, keeping UNKNOWN distinct from zero."""
    return "UNKNOWN" if value is None else f"{float(value):.3f}"


def _cmd_eval_report(args: argparse.Namespace, settings: Settings) -> int:
    from sqlalchemy import select

    database = get_database()
    with database.read_session() as session:
        if args.run_id:
            run = session.get(EvalRun, args.run_id)
        else:
            run = session.scalar(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(1))
        if run is None:
            print("no evaluation runs recorded yet", file=sys.stderr)
            return 1
        dataset = session.get(EvalDataset, run.dataset_id)
        if dataset is None:  # cascade should prevent this; fail loudly if it happens
            print(f"run {run.id} has no dataset row", file=sys.stderr)
            return 1
        results = list(session.scalars(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id)).all())
    payload = _eval_run_payload(run, dataset, results)
    _emit(args, payload, human=_eval_run_human(payload))
    return 0


def _cmd_config(args: argparse.Namespace, settings: Settings) -> int:
    if args.write_env_example:
        print(_env_example())
        return 0
    _emit(args, settings.summary(), human=json.dumps(settings.summary(), indent=2, default=str))
    return 0


def _embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    try:
        return build_embedding_provider(settings)
    except Exception as exc:
        logger.warning(
            "indexing without embeddings: %s", exc, extra=log_extra(provider=settings.embedding_provider)
        )
        return None


def _redact_dsn(dsn: str) -> str:
    if "@" not in dsn:
        return dsn
    scheme, _, remainder = dsn.partition("://")
    return f"{scheme}://***@{remainder.split('@', 1)[1]}"


def _env_example() -> str:
    """Generate .env.example from the Settings model so it cannot drift.

    Two values are deliberately not copied verbatim: the SQLite default embeds an
    absolute path from the machine that generated the file, and secret values must
    never be written to a file that is committed. Both are replaced with portable
    placeholders.
    """
    lines = [
        "# PatchLens configuration example. Copy to .env and edit.",
        "# Generated by `python -m patchlens.cli config --write-env-example` from the",
        "# Settings model, so it cannot drift from the code. Every variable has a",
        "# working default for local development: a fresh checkout runs with an empty",
        "# environment, SQLite, the deterministic mock LLM and hashing embeddings.",
        "",
        "# Relative SQLite paths resolve against the process working directory.",
        "",
    ]
    secrets = {
        name.upper() for name, field in Settings.model_fields.items() if "SecretStr" in str(field.annotation)
    }
    overrides = {
        "DATABASE_URL": "sqlite+pysqlite:///./var/patchlens.db",
        "API_AUTH_TOKEN": "",
        "LLM_API_KEY": "",
        "EMBEDDING_API_KEY": "",
        "GITHUB_WEBHOOK_SECRET": "",
        "GITHUB_PRIVATE_KEY": "",
    }
    for name, field in sorted(Settings.model_fields.items()):
        env_name = name.upper()
        default = field.default
        factory = field.default_factory
        if factory is not None:
            default = cast(Callable[[], object], factory)()
        if isinstance(default, list):
            default = ",".join(str(item) for item in default)
        if default is None:
            default = ""
        description = (field.description or "").strip()
        if env_name in overrides:
            default = overrides[env_name]
        if env_name in secrets and default:
            # A secret default must never be published, whatever the model says.
            default = ""
        if description:
            lines.append(f"# {description}")
        lines.append(f"{env_name}={default}")
        if env_name == "API_AUTH_TOKEN":
            lines.append('# Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"')
        lines.append("")
    return "\n".join(lines)


def _free_port() -> int:  # pragma: no cover - helper for local scripts
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
