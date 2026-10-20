"""Evaluation API contract.

The endpoints must make the same distinction the docs promise: no run is ``not_run``
and an undefined metric is ``null`` (rendered UNKNOWN), never a fabricated zero.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from patchlens.core.config import Settings
from patchlens.services.evaluation.datasets import load_dataset, seed_dataset
from tests.conftest import index_repository, register_repository


def retrieval_document() -> dict:
    return {
        "name": "api-retrieval",
        "version": "1",
        "kind": "retrieval",
        "repository": "patchlens/fixture",
        "commit_sha": "commit-1",
        "cases": [
            {
                "id": "add",
                "query": "add two numbers",
                "relevant": [{"path": "demoapp/calculator.py", "qualified_name": "add", "grade": 3}],
            },
            {
                "id": "divide",
                "query": "divide two numbers",
                "relevant": [{"path": "demoapp/calculator.py", "qualified_name": "divide", "grade": 2}],
            },
        ],
    }


def setup_repository(
    database, app_settings: Settings, root: Path, tmp_path: Path, document: dict
) -> tuple[str, str]:
    with database.session() as session:
        repository = register_repository(session, root)
        index_repository(session, repository, root, app_settings, commit_sha="commit-1")
        path = tmp_path / f"{document['name']}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        dataset = seed_dataset(session, load_dataset(path), source_path=path)
        session.commit()
        return repository.id, dataset.id


def test_datasets_are_not_run_until_a_run_exists(
    client: TestClient, database, app_settings: Settings, demo_repo: Path, tmp_path: Path
) -> None:
    _, dataset_id = setup_repository(database, app_settings, demo_repo, tmp_path, retrieval_document())

    response = client.get("/api/v1/evaluation/datasets")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["id"] == dataset_id
    assert item["status"] == "not_run"
    assert item["latest_run"] is None
    assert item["case_count"] == 2
    assert item["commit_sha"] == "commit-1"
    assert item["repository"] == "patchlens/fixture"

    detail = client.get(f"/api/v1/evaluation/datasets/{dataset_id}")
    assert detail.status_code == 200
    cases = detail.json()["cases"]
    assert [case["case_key"] for case in cases] == ["add", "divide"]
    assert cases[0]["labels"][0]["qualified_name"] == "add"


def test_a_run_is_executed_stored_and_readable(
    client: TestClient, database, app_settings: Settings, demo_repo: Path, tmp_path: Path
) -> None:
    _, dataset_id = setup_repository(database, app_settings, demo_repo, tmp_path, retrieval_document())

    created = client.post("/api/v1/evaluation/runs", json={"dataset_id": dataset_id})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "succeeded"
    assert body["sample_count"] == 2
    assert set(body["strategy_metrics"]) == {"static", "bm25", "semantic", "hybrid", "hybrid_rerank"}
    assert body["strategy_metrics"]["bm25"]["failures"] == 0
    assert body["strategy_metrics"]["bm25"]["cost_usd"] is None
    assert body["metrics"]["best_strategy"] in body["strategy_metrics"]
    assert len(body["results"]) == 2 * 5, "every case/strategy pair is stored"
    assert all(result["predictions"] is not None for result in body["results"])
    run_id = body["id"]

    listing = client.get("/api/v1/evaluation/runs")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [run_id]
    assert listing.json()["items"][0]["dataset_name"] == "api-retrieval"

    detail = client.get(f"/api/v1/evaluation/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == run_id
    assert len(detail.json()["results"]) == 10

    datasets = client.get("/api/v1/evaluation/datasets").json()["items"]
    assert datasets[0]["status"] == "succeeded"
    assert datasets[0]["latest_run"]["id"] == run_id


def test_a_subset_of_strategies_can_be_requested(
    client: TestClient, database, app_settings: Settings, demo_repo: Path, tmp_path: Path
) -> None:
    _, dataset_id = setup_repository(database, app_settings, demo_repo, tmp_path, retrieval_document())
    created = client.post(
        "/api/v1/evaluation/runs",
        json={"dataset_id": dataset_id, "strategies": ["bm25"]},
    )
    assert created.status_code == 201
    assert set(created.json()["strategy_metrics"]) == {"bm25"}
    assert len(created.json()["results"]) == 2


def test_unknown_dataset_is_a_structured_404(client: TestClient) -> None:
    response = client.post("/api/v1/evaluation/runs", json={"dataset_id": "eds_missing"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unknown_strategy_is_a_structured_422(
    client: TestClient, database, app_settings: Settings, demo_repo: Path, tmp_path: Path
) -> None:
    _, dataset_id = setup_repository(database, app_settings, demo_repo, tmp_path, retrieval_document())
    response = client.post(
        "/api/v1/evaluation/runs",
        json={"dataset_id": dataset_id, "strategies": ["telepathy"]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "telepathy" in response.json()["error"]["message"]


def test_dataset_for_an_unregistered_repository_is_rejected(
    client: TestClient, database, app_settings: Settings, demo_repo: Path, tmp_path: Path
) -> None:
    document = retrieval_document()
    document["repository"] = "someone/else"
    _, dataset_id = setup_repository(database, app_settings, demo_repo, tmp_path, document)
    response = client.post("/api/v1/evaluation/runs", json={"dataset_id": dataset_id})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "not registered" in body["error"]["message"]
