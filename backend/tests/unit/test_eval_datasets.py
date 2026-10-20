"""Dataset validation and immutability.

A dataset is evidence: if labels could be edited in place, every recorded run would
silently start referring to different ground truth. These tests pin the validation
that catches mislabelled cases before a run, and the immutability that protects past
runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from patchlens.services.evaluation.datasets import (
    DatasetError,
    content_hash,
    dataset_limitations,
    load_dataset,
    seed_dataset,
)

SIMPLE_DIFF = (
    "diff --git a/pkg/mod.py b/pkg/mod.py\n"
    "--- a/pkg/mod.py\n"
    "+++ b/pkg/mod.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+import os\n"
    "+os.system(input())\n"
)


def retrieval_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "name": "unit-retrieval",
        "version": "1",
        "kind": "retrieval",
        "repository": "patchlens/fixture",
        "commit_sha": "commit-1",
        "cases": [
            {
                "id": "case-1",
                "query": "add two numbers",
                "relevant": [{"path": "demoapp/calculator.py", "qualified_name": "add", "grade": 3}],
            }
        ],
    }
    document.update(overrides)
    return document


def review_document(*, line: int = 3) -> dict[str, object]:
    return {
        "name": "unit-review",
        "version": "1",
        "kind": "review",
        "repository": "patchlens/fixture",
        "cases": [
            {
                "id": "case-1",
                "commit_sha": "commit-1",
                "diff_text": SIMPLE_DIFF,
                "expected": [{"category": "security", "path": "pkg/mod.py", "line": line}],
            }
        ],
    }


def write(path: Path, document: dict[str, object]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_retrieval_dataset_round_trips(tmp_path: Path) -> None:
    dataset = load_dataset(write(tmp_path / "d.json", retrieval_document()))
    assert dataset.kind == "retrieval"
    assert dataset.cases[0].relevant[0].qualified_name == "add"


def test_review_dataset_rejects_a_label_the_diff_did_not_add(tmp_path: Path) -> None:
    path = write(tmp_path / "d.json", review_document(line=5))
    with pytest.raises(DatasetError, match="did not add"):
        load_dataset(path)


def test_review_dataset_rejects_a_label_for_a_path_not_in_the_diff(tmp_path: Path) -> None:
    document = review_document()
    cases = document["cases"]
    assert isinstance(cases, list)
    cases[0]["expected"][0]["path"] = "pkg/other.py"  # type: ignore[index]
    with pytest.raises(DatasetError, match="absent from its diff"):
        load_dataset(write(tmp_path / "d.json", document))


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    document = retrieval_document()
    document["cases"] = [document["cases"][0], document["cases"][0]]  # type: ignore[index]
    with pytest.raises(DatasetError, match="duplicate case id"):
        load_dataset(write(tmp_path / "d.json", document))


def test_retrieval_case_needs_at_least_one_relevant_symbol(tmp_path: Path) -> None:
    document = retrieval_document()
    document["cases"][0]["relevant"] = []  # type: ignore[index]
    with pytest.raises(DatasetError, match="at least one relevant symbol"):
        load_dataset(write(tmp_path / "d.json", document))


def test_unsupported_kind_is_rejected(tmp_path: Path) -> None:
    document = retrieval_document(kind="chat")
    with pytest.raises(DatasetError, match="unsupported kind"):
        load_dataset(write(tmp_path / "d.json", document))


def test_content_hash_is_stable_and_sensitive_to_labels(tmp_path: Path) -> None:
    first = load_dataset(write(tmp_path / "a.json", retrieval_document()))
    # Same document, different JSON key order: the hash must not change.
    reordered = {key: retrieval_document()[key] for key in reversed(list(retrieval_document()))}
    second = load_dataset(write(tmp_path / "b.json", reordered))
    assert content_hash(first) == content_hash(second)

    changed = retrieval_document()
    changed["cases"][0]["relevant"][0]["grade"] = 1  # type: ignore[index]
    third = load_dataset(write(tmp_path / "c.json", changed))
    assert content_hash(third) != content_hash(first)


def test_small_datasets_and_missing_splits_are_reported_as_limitations(tmp_path: Path) -> None:
    dataset = load_dataset(write(tmp_path / "d.json", retrieval_document()))
    limitations = " ".join(dataset_limitations(dataset))
    assert "small dataset" in limitations
    assert "no dev/train split" in limitations


def test_seeding_is_idempotent_and_content_changes_require_a_version_bump(session, tmp_path: Path) -> None:
    path = write(tmp_path / "d.json", retrieval_document())
    dataset = load_dataset(path)
    first = seed_dataset(session, dataset, source_path=path)
    session.flush()
    again = seed_dataset(session, dataset, source_path=path)
    assert again.id == first.id

    changed = retrieval_document()
    changed["cases"][0]["query"] = "different query"  # type: ignore[index]
    with pytest.raises(DatasetError, match="bump the version"):
        seed_dataset(session, load_dataset(write(tmp_path / "changed.json", changed)))
