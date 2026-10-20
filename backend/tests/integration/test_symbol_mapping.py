"""Tests for reading the new side of a diff from a local checkout.

The review engine must analyse the *proposed* revision. If the checkout is still at
the base commit (the normal state when reviewing a pull request locally), the new
side has to be reconstructed from the diff. If the content matches neither side, the
engine must say so rather than analyse unrelated code confidently.
"""

from __future__ import annotations

from pathlib import Path

from patchlens.core.config import Settings
from patchlens.db.models.repository import Repository
from patchlens.services.ingestion.test_mapping import has_plausible_coverage
from patchlens.services.review.diff import parse_diff
from patchlens.services.review.symbol_mapping import map_changed_symbols, read_changed_files
from tests.conftest import index_repository, register_repository
from tests.helpers import NEW_FILES, OLD_FILES, demo_diff, make_diff, write_repo


def local_repository(root: Path | None) -> Repository:
    """An unsaved repository row: ``read_changed_files`` only needs its path."""
    return Repository(
        provider="local",
        owner="patchlens",
        name="fixture",
        full_name="patchlens/fixture",
        default_branch="main",
        local_path=str(root) if root is not None else None,
    )


def test_new_side_is_reconstructed_when_the_checkout_is_at_the_base_revision(
    demo_repo: Path,
) -> None:
    texts, failures, notes = read_changed_files(
        repository=local_repository(demo_repo), diff=parse_diff(demo_diff())
    )

    assert failures == {}
    calculator = texts["demoapp/calculator.py"]
    assert "print(a + b)" in calculator, "the added line must be part of the reviewed content"
    assert "def mean(" in calculator, "the added function must be parseable"
    assert "import os" not in calculator, "the removed import must be gone from the new side"
    assert any("reconstructed from the diff" in note for note in notes)
    # The test module is also changed, so both files are reconstructed.
    assert "test_divide_by_zero_raises" in texts["tests/test_calculator.py"]


def test_checkout_already_at_the_pull_request_head_is_used_verbatim(
    new_demo_repo: Path,
) -> None:
    texts, failures, notes = read_changed_files(
        repository=local_repository(new_demo_repo), diff=parse_diff(demo_diff())
    )

    assert failures == {}
    assert texts["demoapp/calculator.py"] == NEW_FILES["demoapp/calculator.py"]
    assert not any("reconstructed" in note for note in notes), "an up-to-date checkout must not be rewritten"


def test_content_matching_neither_side_is_reported_as_a_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    write_repo(root, OLD_FILES)
    (root / "demoapp/calculator.py").write_text("something_else = 'unrelated'\n", encoding="utf-8")

    texts, failures, notes = read_changed_files(
        repository=local_repository(root), diff=parse_diff(demo_diff())
    )

    assert "demoapp/calculator.py" not in texts
    assert "neither side" in failures["demoapp/calculator.py"]
    assert any("could not be read" in note for note in notes)
    # The untouched test module is still reconstructed and reviewed.
    assert "tests/test_calculator.py" in texts


def test_a_file_added_by_the_diff_is_reconstructed_without_touching_disk(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    write_repo(root, {"pkg/__init__.py": ""})
    diff_text = (
        "diff --git a/pkg/report.py b/pkg/report.py\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/pkg/report.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+def report() -> str:\n"
        "+    return 'ok'\n"
        "+\n"
    )

    texts, failures, notes = read_changed_files(repository=local_repository(root), diff=parse_diff(diff_text))

    assert failures == {}
    assert texts["pkg/report.py"] == "def report() -> str:\n    return 'ok'\n\n"
    assert any("pkg/report.py" in note for note in notes)


def test_path_traversal_in_a_diff_is_rejected_before_any_read(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    (tmp_path / "outside.py").write_text("secret = 1\n", encoding="utf-8")
    diff_text = make_diff("../outside.py", "secret = 0\n", "secret = 1\n")

    texts, failures, notes = read_changed_files(repository=local_repository(root), diff=parse_diff(diff_text))

    assert texts == {}
    assert "path rejected" in failures["../outside.py"], "the attempt must be visible in the report"
    assert notes, "the skipped file is explained rather than silently dropped"


def test_repository_without_a_checkout_explains_the_degradation() -> None:
    texts, failures, notes = read_changed_files(
        repository=local_repository(None), diff=parse_diff(demo_diff())
    )

    assert texts == {}
    assert failures == {}
    assert len(notes) == 1
    assert "no local checkout" in notes[0]


def test_oversized_file_is_skipped_with_a_reason(demo_repo: Path) -> None:
    texts, failures, notes = read_changed_files(
        repository=local_repository(demo_repo),
        diff=parse_diff(demo_diff()),
        max_file_bytes=10,
    )

    assert texts == {}
    assert set(failures) == {"demoapp/calculator.py", "tests/test_calculator.py"}
    assert all("review read limit" in reason for reason in failures.values())
    assert any("could not be read" in note for note in notes)


def test_deleted_files_are_not_read(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    write_repo(root, {"gone.py": "value = 1\n"})
    diff_text = (
        "diff --git a/gone.py b/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-value = 1\n"
    )

    texts, failures, notes = read_changed_files(repository=local_repository(root), diff=parse_diff(diff_text))

    assert texts == {}
    assert failures == {}
    assert notes == []


def _new_file_diff(relative: str, content: str) -> str:
    """A git-style diff for a new file, as ``git diff`` writes it."""
    lines = content.splitlines()
    body = "\n".join(f"+{line}" for line in lines)
    return (
        f"diff --git a/{relative} b/{relative}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{relative}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}\n"
    )


NEW_TEST_FILE = """\"\"\"Tests for the new total helper.\"\"\"

from demoapp.calculator import total


def test_total_sums_values() -> None:
    assert total([1.0, 2.0]) == 3.0
"""


def test_a_test_added_by_the_same_diff_counts_as_coverage(
    session, app_settings: Settings, demo_repo: Path
) -> None:
    """Regression: a method and its new test in one PR looked untested.

    The index only contains the base commit, so tests added by the diff have to be
    parsed from the diff itself. Without that, the review reports
    ``changed-code-without-related-test`` for the very change that adds the test.
    """
    repository = register_repository(session, demo_repo)
    index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
    session.flush()

    base_source = OLD_FILES["demoapp/calculator.py"]
    new_source = (
        base_source.rstrip("\n") + "\n\n\ndef total(values: list[float]) -> float:\n"
        '    """Sum values."""\n'
        "    return sum(values)\n"
    )
    source_diff = make_diff("demoapp/calculator.py", base_source, new_source)
    test_diff = _new_file_diff("tests/test_calculator_total.py", NEW_TEST_FILE)

    without_test = map_changed_symbols(session, repository=repository, diff=parse_diff(source_diff))
    assert "total" in {symbol.qualified_name for symbol in without_test.changed_symbols}
    assert not has_plausible_coverage(without_test.test_matches.get("total", []))

    with_test = map_changed_symbols(session, repository=repository, diff=parse_diff(source_diff + test_diff))
    matches = with_test.test_matches.get("total", [])
    assert matches, "the added test should be associated with the new symbol"
    assert any(match.target.symbol_id.startswith("diff:") for match in matches)
    assert has_plausible_coverage(matches), "the association must suppress the missing-test finding"
    assert any("considered when associating" in note for note in with_test.notes)
