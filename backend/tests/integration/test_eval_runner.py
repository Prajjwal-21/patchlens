"""The evaluation runner against a real SQLite database.

These tests use the real indexer and the real review pipeline; only the provider is
the documented deterministic mock. They assert what was stored, not just the return
value, because the dashboard and CLI read the stored rows.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from patchlens.core.config import Settings
from patchlens.core.enums import RetrievalStrategy
from patchlens.db.models.evaluation import EvalCaseResult, EvalDataset, EvalRun
from patchlens.db.models.repository import Repository
from patchlens.db.models.review import Review
from patchlens.services.evaluation.datasets import load_dataset, seed_dataset
from patchlens.services.evaluation.runner import (
    dataset_cases,
    run_retrieval_evaluation,
    run_review_evaluation,
    run_test_generation_evaluation,
)
from patchlens.services.review.diff import apply_hunks_to_text, parse_diff
from tests.conftest import index_repository, register_repository
from tests.helpers import demo_diff


def seed(tmp_path: Path, session, document: dict) -> EvalDataset:
    path = tmp_path / f"{document['name']}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    dataset = load_dataset(path)
    return seed_dataset(session, dataset, source_path=path)


def retrieval_document(repository: str = "patchlens/fixture", commit_sha: str = "commit-1") -> dict:
    return {
        "name": "it-retrieval",
        "version": "1",
        "kind": "retrieval",
        "repository": repository,
        "commit_sha": commit_sha,
        "cases": [
            {
                "id": "add",
                "query": "add two numbers",
                "relevant": [
                    {"path": "demoapp/calculator.py", "qualified_name": "add", "grade": 3},
                ],
            },
            {
                "id": "divide",
                "query": "divide a number and handle zero",
                "relevant": [
                    {"path": "demoapp/calculator.py", "qualified_name": "divide", "grade": 3},
                ],
            },
        ],
    }


def indexed_fixture(session, app_settings: Settings, demo_repo: Path) -> Repository:
    repository = register_repository(session, demo_repo)
    index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
    session.flush()
    return repository  # type: ignore[return-value]


def test_retrieval_run_records_aggregates_and_every_case(session, app_settings, demo_repo, tmp_path) -> None:
    repository = indexed_fixture(session, app_settings, demo_repo)
    dataset = seed(tmp_path, session, retrieval_document())
    cases = dataset_cases(session, dataset)

    run = run_retrieval_evaluation(
        session,
        dataset=dataset,
        cases=cases,
        repository=repository,
        settings=app_settings,
        strategies=[RetrievalStrategy.BM25, RetrievalStrategy.HYBRID],
        dataset_commit_sha=dataset.commit_sha,
    )
    session.flush()

    assert run.status == "succeeded"
    assert run.sample_count == 2
    assert set(run.strategy_metrics) == {"bm25", "hybrid"}
    for strategy in ("bm25", "hybrid"):
        metrics = run.strategy_metrics[strategy]
        assert metrics["cases"] == 2
        assert metrics["failures"] == 0
        assert metrics["failure_rate"] == 0.0
        assert 0.0 <= metrics["recall@5"] <= 1.0
        assert metrics["latency_ms"]["p50"] is not None
        assert metrics["cost_usd"] is None
    assert run.metrics["best_strategy"] in {"bm25", "hybrid"}
    assert run.metrics["corpus_symbols"] > 0
    assert run.config["repository_version_id"] == repository.latest_version_id

    results = session.scalars(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id)).all()
    assert len(results) == 4, "two cases times two strategies"
    assert all(result.error is None for result in results)
    assert all(isinstance(result.predictions, list) for result in results)
    stored = session.scalar(select(EvalRun).where(EvalRun.id == run.id))
    assert stored is not None and stored.finished_at is not None


def test_labels_for_symbols_absent_from_the_corpus_are_reported(
    session, app_settings, demo_repo, tmp_path
) -> None:
    repository = indexed_fixture(session, app_settings, demo_repo)
    document = retrieval_document()
    document["cases"][0]["relevant"].append(
        {"path": "demoapp/calculator.py", "qualified_name": "does_not_exist", "grade": 1}
    )
    dataset = seed(tmp_path, session, document)
    run = run_retrieval_evaluation(
        session,
        dataset=dataset,
        cases=dataset_cases(session, dataset),
        repository=repository,
        settings=app_settings,
        strategies=[RetrievalStrategy.BM25],
    )
    assert run.metrics["missing_labels"] == ["add:demoapp/calculator.py::does_not_exist"]
    assert any("not in the indexed corpus" in limitation for limitation in run.limitations)


def test_retrieval_run_flags_a_corpus_commit_mismatch(session, app_settings, demo_repo, tmp_path) -> None:
    repository = indexed_fixture(session, app_settings, demo_repo)
    dataset = seed(tmp_path, session, retrieval_document(commit_sha="a-different-commit"))
    run = run_retrieval_evaluation(
        session,
        dataset=dataset,
        cases=dataset_cases(session, dataset),
        repository=repository,
        settings=app_settings,
        strategies=[RetrievalStrategy.BM25],
        dataset_commit_sha=dataset.commit_sha,
    )
    assert any("corpus commit mismatch" in limitation for limitation in run.limitations)


def test_retrieval_run_without_an_index_fails_with_a_reason(
    session, app_settings, demo_repo, tmp_path
) -> None:
    repository = register_repository(session, demo_repo)
    session.flush()
    dataset = seed(tmp_path, session, retrieval_document())
    run = run_retrieval_evaluation(
        session,
        dataset=dataset,
        cases=dataset_cases(session, dataset),
        repository=repository,  # type: ignore[arg-type]
        settings=app_settings,
        strategies=[RetrievalStrategy.BM25],
    )
    assert run.status == "failed"
    assert run.error is not None and "no successfully indexed version" in run.error


def bare_except_anchor(root: Path) -> tuple[str, int]:
    """Find the added ``except:`` on the *new side* of the fixture checkout.

    ``new_side_lines`` is hunk-local, so absolute line numbers must come from the
    reconstructed file, not from an index into that list.
    """
    parsed = parse_diff(demo_diff())
    for file in parsed.files:
        if not file.display_path.endswith("calculator.py"):
            continue
        base_text = (root / file.display_path).read_text(encoding="utf-8")
        new_text = apply_hunks_to_text(file, base_text)
        assert new_text is not None
        lines = new_text.splitlines()
        for number in sorted(file.added_line_numbers):
            if lines[number - 1].strip() == "except:":
                return file.display_path, number
    raise AssertionError("fixture diff no longer adds a bare except")


def review_document(root: Path) -> dict:
    path, line = bare_except_anchor(root)
    return {
        "name": "it-review",
        "version": "1",
        "kind": "review",
        "repository": "patchlens/fixture",
        "cases": [
            {
                "id": "bare-except",
                "commit_sha": "commit-1",
                "diff_text": demo_diff(),
                "expected": [
                    {
                        "category": "correctness",
                        "rule_id": "bare-except",
                        "path": path,
                        "line": line,
                        "must_find": True,
                    }
                ],
            }
        ],
    }


def test_review_run_scores_findings_and_bypasses_the_review_cache(
    session, app_settings, demo_repo, tmp_path
) -> None:
    indexed_fixture(session, app_settings, demo_repo)
    dataset = seed(tmp_path, session, review_document(demo_repo))

    first = run_review_evaluation(
        session,
        dataset=dataset,
        cases=dataset_cases(session, dataset),
        repository=_repository(session),
        settings=app_settings,
    )
    session.flush()
    second = run_review_evaluation(
        session,
        dataset=dataset,
        cases=dataset_cases(session, dataset),
        repository=_repository(session),
        settings=app_settings,
    )
    session.flush()

    assert first.status == "succeeded", first.error
    assert second.status == "succeeded", second.error
    assert first.metrics["provider_is_mock"] is True
    assert first.metrics["detected"] == ["bare-except"]
    assert first.metrics["overall"]["tp"] >= 1
    assert first.metrics["rule_match_rate"] == 1.0
    assert first.metrics["must_find"] == {"total": 1, "missed": 0, "recall": 1.0}

    first_result = session.scalar(select(EvalCaseResult).where(EvalCaseResult.run_id == first.id))
    second_result = session.scalar(select(EvalCaseResult).where(EvalCaseResult.run_id == second.id))
    assert first_result is not None and second_result is not None
    first_review = first_result.metrics["review_id"]
    second_review = second_result.metrics["review_id"]
    assert first_review != second_review, "an evaluation must not be answered from a stored review"

    reviews = session.scalars(select(Review).where(Review.trigger == "evaluation")).all()
    assert {review.id for review in reviews} >= {first_review, second_review}
    first_row = session.get(Review, first_review)
    second_row = session.get(Review, second_review)
    assert first_row is not None and first_row.commit_sha.endswith(f"-eval-{first.id}")
    assert second_row is not None and second_row.commit_sha.endswith(f"-eval-{second.id}")


def test_static_ranking_does_not_depend_on_index_time_symbol_ids(
    session, app_settings, demo_repo, tmp_path
) -> None:
    """The same corpus must rank the same after an independent re-index.

    Symbol ids are generated at indexing time. Breaking ties with them made the
    ``static`` baseline score differently on two identical corpora, which turns a
    random id into part of the evaluation result.
    """
    first = indexed_fixture(session, app_settings, demo_repo)
    second_root = tmp_path / "copy"
    shutil.copytree(demo_repo, second_root)
    second = register_repository(
        session, second_root, name="fixture-copy", full_name="patchlens/fixture-copy"
    )
    index_repository(session, second, second_root, app_settings, commit_sha="commit-1")
    session.flush()

    dataset = seed(tmp_path, session, retrieval_document())
    cases = dataset_cases(session, dataset)
    first_run = run_retrieval_evaluation(
        session,
        dataset=dataset,
        cases=cases,
        repository=first,
        settings=app_settings,
        strategies=[RetrievalStrategy.STATIC],
    )
    second_run = run_retrieval_evaluation(
        session,
        dataset=dataset,
        cases=cases,
        repository=second,  # type: ignore[arg-type]
        settings=app_settings,
        strategies=[RetrievalStrategy.STATIC],
    )
    session.flush()

    first_metrics = first_run.strategy_metrics["static"]
    second_metrics = second_run.strategy_metrics["static"]
    for key in ("recall@5", "mrr@10", "ndcg@10"):
        assert first_metrics[key] == pytest.approx(second_metrics[key]), key

    def rankings(run_id: str) -> list[list[tuple[str, str]]]:
        rows = session.scalars(
            select(EvalCaseResult).where(EvalCaseResult.run_id == run_id).order_by(EvalCaseResult.case_key)
        ).all()
        return [[(item["path"], item["qualified_name"]) for item in row.predictions] for row in rows]

    assert rankings(first_run.id) == rankings(second_run.id)


def _repository(session) -> Repository:
    repository = session.scalar(select(Repository).where(Repository.full_name == "patchlens/fixture"))
    assert repository is not None
    return repository


def generation_document() -> dict:
    return {
        "name": "it-test-generation",
        "version": "1",
        "kind": "test_generation",
        "repository": "patchlens/fixture",
        "commit_sha": "commit-1",
        "cases": [
            {
                "id": "add",
                "target_path": "eval_targets/arithmetic.py",
                "target_symbol": "add",
                "source": "def add(a: int, b: int) -> int:\n    return a + b\n",
                "signature": "def add(a: int, b: int) -> int",
                "mutations": [
                    {
                        "id": "add-subtracts",
                        "mutated_source": "def add(a: int, b: int) -> int:\n    return a - b\n",
                    }
                ],
            }
        ],
    }


def test_test_generation_run_records_unknown_execution_without_a_sandbox(
    session, app_settings, demo_repo, tmp_path
) -> None:
    repository = register_repository(session, demo_repo)
    session.flush()
    dataset = seed(tmp_path, session, generation_document())
    run = run_test_generation_evaluation(
        session,
        dataset=dataset,
        cases=dataset_cases(session, dataset),
        repository=repository,  # type: ignore[arg-type]
        settings=app_settings,
    )
    session.flush()
    assert run.status == "succeeded", run.error
    assert run.metrics["validity_rate"] == 1.0
    assert run.metrics["executed"] == 0
    assert run.metrics["execution_rate"] is None
    assert run.metrics["pass_rate_on_original"] is None
    assert run.metrics["regression_detection_rate"] is None
    assert run.metrics["cost_usd"] is None
    assert run.metrics["provider_is_mock"] is True
    assert any("UNKNOWN" in unknown for unknown in run.metrics["unknowns"])
    assert any("UNKNOWN" in limitation for limitation in run.limitations)
    result = session.scalar(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id))
    assert result is not None
    assert result.strategy == "test_generation"
    assert result.metrics["valid"] == 1
    assert result.metrics["executed"] is False


def test_test_generation_run_executes_and_scores_mutations_when_opted_in(
    session, app_settings, demo_repo, tmp_path
) -> None:
    repository = register_repository(session, demo_repo)
    session.flush()
    dataset = seed(tmp_path, session, generation_document())
    settings = app_settings.model_copy(
        update={"test_execution_mode": "subprocess", "allow_local_test_execution": True}
    )
    run = run_test_generation_evaluation(
        session,
        dataset=dataset,
        cases=dataset_cases(session, dataset),
        repository=repository,  # type: ignore[arg-type]
        settings=settings,
    )
    session.flush()
    assert run.status == "succeeded", run.error
    assert run.metrics["validity_rate"] == 1.0
    assert run.metrics["execution_rate"] == 1.0
    result = session.scalar(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id))
    assert result is not None
    assert result.metrics["executed"] is True
    # The deterministic mock's smoke suite passes but does not observe behaviour, so
    # it cannot detect the mutation. This is a measured result, not an execution error.
    assert result.metrics["passed_on_original"] is True
    assert run.metrics["pass_rate_on_original"] == 1.0
    assert result.metrics["mutations_evaluated"] == 1
    assert result.metrics["mutations_detected"] == 0
    assert run.metrics["regression_detection_rate"] == 0.0
    assert any("cost_usd" in unknown for unknown in run.metrics["unknowns"])
    assert run.metrics["execution_latency_ms"]["p50"] is not None
