"""The evaluation CLI wrappers, exercised through ``main``.

The runner is tested directly elsewhere; this checks the operator-facing path:
seeding a directory of datasets, running one, and reading the recorded run back.
"""

from __future__ import annotations

import json
from pathlib import Path

from patchlens.cli import main
from tests.conftest import index_repository, register_repository


def document() -> dict:
    return {
        "name": "cli-retrieval",
        "version": "1",
        "kind": "retrieval",
        "repository": "patchlens/fixture",
        "commit_sha": "commit-1",
        "cases": [
            {
                "id": "add",
                "query": "add two numbers",
                "relevant": [{"path": "demoapp/calculator.py", "qualified_name": "add", "grade": 3}],
            }
        ],
    }


def test_seed_run_and_report(app_settings, database, demo_repo: Path, tmp_path: Path, capsys) -> None:
    with database.session() as session:
        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()

    path = tmp_path / "cli-retrieval.json"
    path.write_text(json.dumps(document()), encoding="utf-8")

    assert main(["eval-seed", "--path", str(tmp_path)]) == 0
    seeded = capsys.readouterr().out
    assert "seeded" in seeded
    assert "cli-retrieval v1" in seeded

    assert main(["eval-run", "--dataset", "cli-retrieval", "--strategies", "bm25"]) == 0
    run_output = capsys.readouterr().out
    assert "succeeded" in run_output
    assert "bm25" in run_output
    assert "best strategy: bm25" in run_output
    assert "UNKNOWN" not in run_output  # every metric for this case is defined

    assert main(["--json", "eval-report"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset"]["name"] == "cli-retrieval"
    assert payload["status"] == "succeeded"
    assert payload["strategy_metrics"]["bm25"]["cases"] == 1
    assert len(payload["cases"]) == 1


def test_seeding_the_same_dataset_twice_is_reported_as_unchanged(
    app_settings, database, demo_repo: Path, tmp_path: Path, capsys
) -> None:
    path = tmp_path / "cli-retrieval.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    assert main(["eval-seed", "--path", str(path)]) == 0
    capsys.readouterr()
    assert main(["eval-seed", "--path", str(path)]) == 0
    assert "unchanged" in capsys.readouterr().out


def test_unknown_dataset_exits_non_zero(app_settings, database, capsys) -> None:
    assert main(["eval-run", "--dataset", "does-not-exist"]) == 1
    assert "not seeded" in capsys.readouterr().err
