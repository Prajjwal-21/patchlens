"""Unit tests for the unified-diff parser.

The parser is the trust anchor for every location the review engine later cites,
so these tests cover the awkward shapes real diffs contain: renames, binary files,
missing trailing newlines and malformed hunk headers.
"""

from __future__ import annotations

from patchlens.services.review.diff import parse_diff


def test_parses_added_and_removed_line_numbers() -> None:
    diff = (
        "diff --git a/app.py b/app.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,4 +1,5 @@\n"
        " import os\n"
        "+import sys\n"
        " \n"
        " def main():\n"
        "-    return 1\n"
        "+    return sys.exit(0)\n"
    )
    parsed = parse_diff(diff)
    assert parsed.changed_files == ["app.py"]
    assert parsed.added_lines == 2
    assert parsed.removed_lines == 1
    assert parsed.added_lines_for("app.py") == {2, 5}
    assert parsed.added_line_text("app.py", 2) == "import sys"
    # Context lines are commentable on the new side; removed lines are not.
    assert parsed.commentable_lines("app.py") == {1, 2, 3, 4, 5}
    assert parsed.warnings == []


def test_rename_is_recorded_with_old_and_new_paths() -> None:
    diff = (
        "diff --git a/old/name.py b/new/name.py\n"
        "similarity index 90%\n"
        "rename from old/name.py\n"
        "rename to new/name.py\n"
        "--- a/old/name.py\n"
        "+++ b/new/name.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def f():\n"
        "+    return 1\n"
        "-    pass\n"
    )
    parsed = parse_diff(diff)
    file = parsed.files[0]
    assert file.status == "renamed"
    assert file.display_path == "new/name.py"
    assert file.old_path == "old/name.py"
    assert parsed.added_lines_for("new/name.py") == {2}


def test_binary_file_is_marked_and_has_no_lines() -> None:
    diff = (
        "diff --git a/logo.png b/logo.png\n"
        "new file mode 100644\n"
        "index 0000000..3333333\n"
        "Binary files /dev/null and b/logo.png differ\n"
    )
    parsed = parse_diff(diff)
    assert parsed.files[0].is_binary is True
    assert parsed.files[0].added_line_count == 0
    assert parsed.added_lines_for("logo.png") == set()


def test_deleted_file_status() -> None:
    diff = (
        "diff --git a/gone.py b/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def f():\n"
        "-    pass\n"
    )
    parsed = parse_diff(diff)
    assert parsed.files[0].status == "deleted"
    assert parsed.added_lines_for("gone.py") == set()


def test_no_newline_marker_does_not_shift_line_numbers() -> None:
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "\\ No newline at end of file\n"
        "+new\n"
    )
    parsed = parse_diff(diff)
    assert parsed.added_line_text("app.py", 1) == "new"
    assert parsed.added_lines == 1


def test_malformed_hunk_header_is_a_warning_not_a_crash() -> None:
    diff = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ nonsense @@\n+import sys\n"
    parsed = parse_diff(diff)
    assert parsed.warnings, "a malformed hunk must be reported"
    assert parsed.files[0].added_line_count == 0


def test_empty_and_whitespace_diffs_are_empty() -> None:
    for text in ("", "   \n  "):
        parsed = parse_diff(text)
        assert parsed.is_empty
        assert parsed.warnings


def test_file_lookup_tolerates_prefix_variants() -> None:
    diff = (
        "diff --git a/pkg/mod.py b/pkg/mod.py\n"
        "--- a/pkg/mod.py\n"
        "+++ b/pkg/mod.py\n"
        "@@ -1 +1,2 @@\n"
        " x = 1\n"
        "+y = 2\n"
    )
    parsed = parse_diff(diff)
    assert parsed.file_for("./pkg/mod.py") is not None
    assert parsed.added_lines_for("pkg/mod.py") == {2}
    assert parsed.added_line_text("pkg/mod.py", 99) is None
    assert parsed.added_line_text("missing.py", 1) is None


def test_render_added_excerpt_contains_added_lines_only_and_respects_budget() -> None:
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,3 +1,4 @@\n"
        " context one\n"
        "+added one\n"
        " context two\n"
        "+added two\n"
    )
    parsed = parse_diff(diff)
    from patchlens.services.review.diff import render_added_excerpt

    excerpt = render_added_excerpt(parsed, max_chars=10_000, max_lines=10)
    assert "added one" in excerpt
    assert "added two" in excerpt
    assert "context one" not in excerpt

    tiny = render_added_excerpt(parsed, max_chars=40, max_lines=10)
    assert len(tiny) <= 200  # header plus first chunk only


def test_under_counted_hunk_header_keeps_the_extra_lines() -> None:
    """A header that lies must not silently drop the tail of a file.

    Hand-written diffs occasionally under-count. Trusting the count would truncate
    the new side, and AST checks would then analyse an incomplete file without any
    indication that they had; keeping the lines and warning is the honest failure
    mode.
    """
    diff = (
        "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n first = 1\n+second = 2\n"
    )
    parsed = parse_diff(diff)
    file = parsed.files[0]
    assert parsed.added_lines_for("app.py") == {2}
    assert parsed.added_lines == 1
    assert file.hunks[0].truncated is True
    assert any("under-counted" in warning for warning in parsed.warnings)


def test_over_counted_hunk_header_is_flagged_at_end_of_input() -> None:
    diff = (
        "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,5 +1,5 @@\n first = 1\n+second = 2\n"
    )
    parsed = parse_diff(diff)
    assert parsed.added_lines == 1
    assert parsed.files[0].hunks[0].truncated is True
    assert any("disagree" in warning for warning in parsed.warnings)


def test_apply_hunks_reconstructs_the_new_side_from_the_old_side() -> None:
    from patchlens.services.review.diff import (
        apply_hunks_to_text,
        new_side_lines,
        new_side_matches,
    )

    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,4 +1,8 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
        "+\n"
        "+def sub(a, b):\n"
        "+\n"
        "+    return a - b\n"
        " \n"
        " # trailing context\n"
    )
    parsed = parse_diff(diff)
    file = parsed.files[0]
    base = "def add(a, b):\n    return a - b\n\n# trailing context\n"

    assert new_side_matches(file, base) is False, "the base revision is not the new side"
    reconstructed = apply_hunks_to_text(file, base)
    assert reconstructed is not None
    assert new_side_matches(file, reconstructed) is True
    assert "def sub(a, b):" in reconstructed
    assert reconstructed.endswith("# trailing context\n")
    assert new_side_lines(file)[:3] == ["def add(a, b):", "    return a + b", ""]


def test_apply_hunks_refuses_content_matching_neither_side() -> None:
    from patchlens.services.review.diff import apply_hunks_to_text

    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,3 +1,4 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
        " \n"
        " # end\n"
    )
    parsed = parse_diff(diff)
    unrelated = "something = 'entirely different'\n"
    assert apply_hunks_to_text(parsed.files[0], unrelated) is None
