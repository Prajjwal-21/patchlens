"""Evaluation runner: the only code path that writes ``eval_runs``.

Two kinds of run share the same envelope:

* ``retrieval`` sweeps strategies over one indexed corpus and scores ranked lists;
* ``review`` runs the real review pipeline over labelled diffs and scores findings.

Neither invents a number. Aggregates are computed from the per-case rows that are
written in the same transaction, so any aggregate can be recomputed from the stored
detail, and an undefined metric stays ``None`` (rendered as ``UNKNOWN`` downstream).
"""

from __future__ import annotations

import platform
import subprocess
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from patchlens import __version__
from patchlens.core.config import Settings
from patchlens.core.enums import EvalRunStatus, RetrievalStrategy
from patchlens.core.logging import describe_exception, get_logger, log_extra
from patchlens.db.models.evaluation import EvalCase, EvalCaseResult, EvalDataset, EvalRun
from patchlens.db.models.repository import Repository
from patchlens.services.embedding.base import EmbeddingProvider
from patchlens.services.evaluation.datasets import load_cases
from patchlens.services.evaluation.metrics import (
    aggregate_retrieval,
    mean,
    mrr_at_k,
    ndcg_at_k,
    percentile,
    precision_recall_f1,
    recall_at_k,
    score_category,
)
from patchlens.services.llm.base import LLMProvider
from patchlens.services.providers.factory import build_embedding_provider
from patchlens.services.retrieval.service import CodeRetriever, resolve_version
from patchlens.services.review.diff import parse_diff
from patchlens.services.review.pipeline import ReviewPipeline, ReviewRequest

logger = get_logger(__name__)

RECALL_K = 5
MRR_K = 10
NDCG_K = 10
RANK_K = max(RECALL_K, MRR_K, NDCG_K)


def code_revision() -> str:
    """Best-effort identifier of the running code, for run provenance."""
    try:
        # Fixed argv, no shell, 5s timeout. A missing git is not an error: the run
        # is still valid, it simply cannot name the code revision.
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - PATH lookup is intended
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else "uncommitted"


def _settings_snapshot(settings: Settings) -> dict[str, Any]:
    return {
        "retrieval_strategy": settings.retrieval_strategy,
        "retrieval_top_k": settings.retrieval_top_k,
        "retrieval_candidate_k": settings.retrieval_candidate_k,
        "retrieval_rrf_k": settings.retrieval_rrf_k,
        "retrieval_bm25_k1": settings.retrieval_bm25_k1,
        "retrieval_bm25_b": settings.retrieval_bm25_b,
        "reranker": settings.reranker,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model if settings.llm_provider != "mock" else "deterministic-mock",
        "llm_is_mock": settings.llm_is_mock,
    }


def _limitations(dataset: EvalDataset, *extra: str) -> list[str]:
    """Dataset limitations plus run-specific notes, one entry per line."""
    stored = [line.strip() for line in (dataset.limitations or "").splitlines() if line.strip()]
    return [*stored, *extra]


def _start_run(
    session: Session,
    *,
    dataset: EvalDataset,
    kind: str,
    settings: Settings,
    config: dict[str, Any],
    limitations: Sequence[str],
) -> EvalRun:
    run = EvalRun(
        dataset_id=dataset.id,
        kind=kind,
        status=EvalRunStatus.RUNNING.value,
        config={
            **config,
            "settings": _settings_snapshot(settings),
            "dataset_content_hash": dataset.content_hash,
            "code_revision": code_revision(),
            "patchlens_version": __version__,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        limitations=list(limitations),
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()  # the id is part of the config the caller wanted to record
    return run


def _finish_run(
    session: Session,
    run: EvalRun,
    *,
    metrics: dict[str, Any],
    strategy_metrics: dict[str, Any],
    sample_count: int,
) -> EvalRun:
    finished = datetime.now(UTC)
    run.status = EvalRunStatus.SUCCEEDED.value
    run.metrics = metrics
    run.strategy_metrics = strategy_metrics
    run.sample_count = sample_count
    run.finished_at = finished
    run.duration_ms = int((finished - run.started_at).total_seconds() * 1000) if run.started_at else None
    session.add(run)
    return run


def _fail_run(session: Session, run: EvalRun, error: str) -> None:
    run.status = EvalRunStatus.FAILED.value
    run.error = error[:4_000]
    run.finished_at = datetime.now(UTC)
    session.add(run)


def run_retrieval_evaluation(
    session: Session,
    *,
    dataset: EvalDataset,
    cases: Sequence[EvalCase],
    repository: Repository,
    settings: Settings,
    strategies: Sequence[RetrievalStrategy],
    embedding_provider: EmbeddingProvider | None = None,
    dataset_commit_sha: str | None = None,
) -> EvalRun:
    """Score every strategy on the same corpus, queries and labels."""
    limitations = _limitations(
        dataset,
        "retrieval metrics depend on the indexed corpus in this database; a re-index "
        "changes the corpus and invalidates comparison with earlier runs",
    )
    if embedding_provider is None:
        try:
            embedding_provider = build_embedding_provider(settings)
        except Exception as exc:  # build failure is a recorded limitation, not a crash
            limitations.append(
                f"embedding provider could not be built ({exc}); semantic strategies will "
                "record a failure instead of a score"
            )
    run = _start_run(
        session,
        dataset=dataset,
        kind="retrieval",
        settings=settings,
        config={
            "strategies": [strategy.value for strategy in strategies],
            "top_k_scored": RANK_K,
            "repository": repository.full_name,
            "repository_version_id": None,
            "dataset_commit_sha": dataset_commit_sha,
        },
        limitations=limitations,
    )
    retriever = CodeRetriever(session, settings=settings, embedding_provider=embedding_provider)
    version = resolve_version(session, repository)
    if version is None:
        _fail_run(session, run, "repository has no successfully indexed version to evaluate against")
        return run

    if dataset_commit_sha and version.commit_sha and version.commit_sha != dataset_commit_sha:
        run.limitations = [
            *run.limitations,
            f"corpus commit mismatch: dataset labels refer to {dataset_commit_sha}, the indexed "
            f"version is {version.commit_sha}",
        ]
    corpus = retriever.corpus_for(version)
    corpus_keys = {document.key() for document in corpus.documents}
    run.config = {
        **run.config,
        "repository_version_id": version.id,
        "corpus_symbols": len(corpus.documents),
        "corpus_commit_sha": version.commit_sha,
    }

    per_case: dict[str, list[dict[str, Any]]] = {strategy.value: [] for strategy in strategies}
    warnings: list[str] = []
    missing_labels: list[str] = []
    for case in cases:
        grades = {
            (str(label["path"]), str(label["qualified_name"])): int(label["grade"]) for label in case.labels
        }
        relevant = set(grades)
        # A label that names a symbol the corpus does not contain is a broken
        # dataset, not a retrieval failure. It is reported loudly and kept in the
        # denominator rather than quietly dropped.
        case_missing = sorted(f"{path}::{name}" for path, name in grades if (path, name) not in corpus_keys)
        missing_labels.extend(f"{case.case_key}:{item}" for item in case_missing)
        for strategy in strategies:
            started = time.perf_counter()
            error: str | None = None
            predictions: list[dict[str, Any]] = []
            try:
                bundle = retriever.retrieve(
                    repository=repository,
                    queries=[case.query],
                    strategy=strategy,
                    top_k=RANK_K,
                )
                predictions = [
                    {
                        "symbol_id": item.symbol_id,
                        "path": item.path,
                        "qualified_name": item.qualified_name,
                        "score": round(item.score, 6),
                        "is_test": item.is_test,
                    }
                    for item in bundle.result.results[:RANK_K]
                ]
                for warning in bundle.warnings:
                    # The retrieval bundle warns when no tests rank into the candidate
                    # set. That is useful during a review, but it is not a retrieval
                    # failure and would otherwise be attached to every eval run.
                    if "no related tests were retrieved" in warning:
                        continue
                    if warning not in warnings:
                        warnings.append(warning)
            except Exception as exc:  # a strategy that breaks must not abort the sweep
                error = f"{describe_exception(exc)['type']}: {exc}"
                logger.warning(
                    "retrieval evaluation case failed",
                    extra=log_extra(dataset=dataset.name, strategy=strategy.value, case=case.case_key),
                )
            latency_ms = (time.perf_counter() - started) * 1000
            keys = [(item["path"], item["qualified_name"]) for item in predictions]
            metrics: dict[str, Any] = {
                f"recall@{RECALL_K}": recall_at_k(keys, relevant, RECALL_K),
                f"mrr@{MRR_K}": mrr_at_k(keys, relevant, MRR_K),
                f"ndcg@{NDCG_K}": ndcg_at_k(keys, grades, NDCG_K),
                "latency_ms": latency_ms,
                "missing_labels": case_missing,
            }
            session.add(
                EvalCaseResult(
                    run_id=run.id,
                    case_id=case.id,
                    case_key=case.case_key,
                    split=case.split,
                    strategy=strategy.value,
                    predictions=predictions,
                    metrics=metrics,
                    latency_ms=latency_ms,
                    error=error,
                )
            )
            per_case[strategy.value].append({**metrics, "error": error})

    strategy_metrics = {
        strategy.value: aggregate_retrieval(
            per_case[strategy.value], k_recall=RECALL_K, k_mrr=MRR_K, k_ndcg=NDCG_K
        )
        for strategy in strategies
    }
    scored = {
        name: metrics
        for name, metrics in strategy_metrics.items()
        if metrics[f"recall@{RECALL_K}"] is not None
    }
    best = (
        max(
            scored,
            key=lambda name: (
                scored[name][f"recall@{RECALL_K}"] or 0.0,
                scored[name][f"mrr@{MRR_K}"] or 0.0,
            ),
        )
        if scored
        else None
    )
    if missing_labels:
        run.limitations = [
            *run.limitations,
            f"{len(missing_labels)} dataset label(s) name symbols that are not in the indexed "
            "corpus: " + ", ".join(missing_labels[:10]),
        ]
    if warnings:
        run.notes = "; ".join(warnings[:10])
    metrics = {
        "k": {"recall": RECALL_K, "mrr": MRR_K, "ndcg": NDCG_K},
        "case_count": len(cases),
        "corpus_symbols": len(corpus.documents),
        "embedding_is_neural": getattr(embedding_provider, "is_neural", None)
        if embedding_provider is not None
        else None,
        "best_strategy": best,
        "missing_labels": missing_labels[:20],
        "unknowns": [
            "cost_usd is UNKNOWN: no provider-reported token usage is available for local retrieval",
        ],
    }
    return _finish_run(
        session,
        run,
        metrics=metrics,
        strategy_metrics=strategy_metrics,
        sample_count=len(cases),
    )


def _finding_prediction(finding: Any) -> dict[str, Any]:
    return {
        "rule_id": finding.rule_id,
        "category": finding.category,
        "path": finding.path,
        "start_line": finding.start_line,
        "end_line": finding.end_line,
        "severity": finding.severity,
        "verification_status": finding.verification_status,
        "title": finding.title,
    }


def run_review_evaluation(
    session: Session,
    *,
    dataset: EvalDataset,
    cases: Sequence[EvalCase],
    repository: Repository,
    settings: Settings,
    llm_provider: LLMProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> EvalRun:
    """Run the real review pipeline over labelled diffs and score the findings.

    Each review uses a run-scoped commit sha so the pipeline cannot answer from a
    previously stored review: an evaluation must measure the code under test, not the
    cache in front of it.
    """
    limitations = _limitations(
        dataset,
        "findings are scored against author-labelled expectations; a defect the author "
        "did not label counts as a false positive",
        "review runs use the configured provider; with the mock provider the numbers "
        "measure the deterministic rule engine, not an LLM",
    )
    run = _start_run(
        session,
        dataset=dataset,
        kind="review",
        settings=settings,
        config={
            "repository": repository.full_name,
            "case_count": len(cases),
        },
        limitations=limitations,
    )

    totals: dict[str, dict[str, int]] = {}
    matched_total = 0
    rule_matches = 0
    latencies: list[float] = []
    rejected_total = 0
    must_find_total = 0
    must_find_missed = 0
    detected: list[str] = []
    missed: list[str] = []

    try:
        pipeline = ReviewPipeline(
            session,
            settings=settings,
            llm_provider=llm_provider,
            embedding_provider=embedding_provider,
        )
        for case in cases:
            payload = case.payload or {}
            diff_text = str(payload.get("diff_text") or "")
            if not diff_text:
                raise ValueError(f"case {case.case_key} has no diff_text payload")
            commit_sha = str(payload.get("commit_sha") or dataset.name)
            expected = list(payload.get("expected") or [])
            started = time.perf_counter()
            review_result = pipeline.run(
                ReviewRequest(
                    repository=repository,
                    diff_text=diff_text,
                    commit_sha=f"{commit_sha}-eval-{run.id}",
                    trigger="evaluation",
                )
            )
            latency_ms = (time.perf_counter() - started) * 1000
            findings = list(review_result.findings)
            predictions = [
                _finding_prediction(finding)
                for finding in findings
                if finding.verification_status != "rejected"
            ]
            rejected = [finding for finding in findings if finding.verification_status == "rejected"]
            rejected_total += len(rejected)
            scored = score_category(expected, predictions)
            for category, counts in scored["categories"].items():
                bucket = totals.setdefault(category, {"tp": 0, "fp": 0, "fn": 0})
                for key in bucket:
                    bucket[key] += counts[key]
            case_must_find = [label for label in expected if label.get("must_find")]
            case_must_find_missed = [label for label in scored["missed"] if label.get("must_find")]
            must_find_total += len(case_must_find)
            must_find_missed += len(case_must_find_missed)
            matched_total += len(scored["matched"])
            rule_matches += sum(1 for pair in scored["matched"] if pair["rule_match"])
            detected.extend(
                str(pair["expected"].get("rule_id") or pair["expected"]["category"])
                for pair in scored["matched"]
            )
            missed.extend(str(label.get("rule_id") or label["category"]) for label in scored["missed"])
            latencies.append(latency_ms)
            session.add(
                EvalCaseResult(
                    run_id=run.id,
                    case_id=case.id,
                    case_key=case.case_key,
                    split=case.split,
                    strategy="review_pipeline",
                    predictions=predictions,
                    metrics={
                        **scored,
                        "latency_ms": latency_ms,
                        "expected_count": len(expected),
                        "rejected_count": len(rejected),
                        "must_find_total": len(case_must_find),
                        "must_find_missed": len(case_must_find_missed),
                        "review_id": review_result.review.id,
                        "review_status": review_result.review.status,
                    },
                    latency_ms=latency_ms,
                    error=None if review_result.review.status == "succeeded" else review_result.review.error,
                )
            )
    except Exception as exc:
        detail = describe_exception(exc)
        _fail_run(session, run, f"{detail['type']}: {detail['message']}")
        logger.error(
            "review evaluation failed",
            extra=log_extra(dataset=dataset.name, **detail),
        )
        return run

    category_metrics = {
        category: {**counts, **precision_recall_f1(counts["tp"], counts["fp"], counts["fn"])}
        for category, counts in sorted(totals.items())
    }
    micro = {
        "tp": sum(counts["tp"] for counts in totals.values()),
        "fp": sum(counts["fp"] for counts in totals.values()),
        "fn": sum(counts["fn"] for counts in totals.values()),
    }
    metrics = {
        "case_count": len(cases),
        "categories": category_metrics,
        "overall": {**micro, **precision_recall_f1(micro["tp"], micro["fp"], micro["fn"])},
        "matched_findings": matched_total,
        "rule_match_rate": (rule_matches / matched_total) if matched_total else None,
        "rejected_total": rejected_total,
        "must_find": {
            "total": must_find_total,
            "missed": must_find_missed,
            # Undefined when no label is marked must_find; never reported as zero.
            "recall": ((must_find_total - must_find_missed) / must_find_total if must_find_total else None),
        },
        "latency_ms": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95)},
        "mean_latency_ms": mean(latencies),
        "detected": sorted(set(detected)),
        "missed_expected": sorted(set(missed)),
        "provider_is_mock": settings.llm_is_mock,
        "unknowns": [
            "invalid-location rate is not a separate metric here: the validation boundary "
            "stores unverifiable candidates as rejected, and rejected_total is reported instead",
            "no human review of the predictions was performed as part of this run",
        ],
    }
    return _finish_run(
        session,
        run,
        metrics=metrics,
        strategy_metrics={},
        sample_count=len(cases),
    )


def dataset_cases(session: Session, dataset: EvalDataset) -> list[EvalCase]:
    """Cases in a stable order, so two runs are comparable case by case."""
    return load_cases(session, dataset)


def run_findings_summary(case_results: Sequence[EvalCaseResult]) -> list[dict[str, Any]]:
    """Compact per-case view used by the API and the CLI report."""
    return [
        {
            "case_key": result.case_key,
            "strategy": result.strategy,
            "split": result.split,
            "latency_ms": result.latency_ms,
            "error": result.error,
            "metrics": {
                key: value
                for key, value in result.metrics.items()
                if key
                in {
                    "precision",
                    "recall",
                    "f1",
                    "latency_ms",
                    "expected_count",
                    "must_find_total",
                    "must_find_missed",
                    "matched",
                }
                or key.startswith(("recall@", "mrr@", "ndcg@"))
            },
            "prediction_count": len(result.predictions),
        }
        for result in case_results
    ]


def verify_case_diffs(cases: Sequence[EvalCase]) -> list[str]:
    """Sanity-check that stored review payloads still parse. Returns warnings."""
    warnings: list[str] = []
    for case in cases:
        if (case.payload or {}).get("kind") != "review":
            continue
        parsed = parse_diff(str((case.payload or {}).get("diff_text") or ""))
        if parsed.is_empty:
            warnings.append(f"case {case.case_key}: stored diff no longer parses")
        elif parsed.warnings:
            warnings.append(f"case {case.case_key}: diff parsed with warnings: {parsed.warnings[:2]}")
    return warnings


__all__ = [
    "MRR_K",
    "NDCG_K",
    "RANK_K",
    "RECALL_K",
    "code_revision",
    "dataset_cases",
    "run_findings_summary",
    "run_retrieval_evaluation",
    "run_review_evaluation",
    "verify_case_diffs",
]
