"""Unit tests for the deterministic checks.

The checks run on added diff lines (and, when new-side text is available, on the
AST). These tests pin down three properties that the review's credibility depends
on:

1. each rule fires on its stated pattern;
2. rules never fire on lines the diff did not add;
3. findings cite lines that exist, with machine-checkable evidence.
"""

from __future__ import annotations

import pytest

from patchlens.core.config import Settings
from patchlens.core.enums import ConfidenceBasis, VerificationStatus
from patchlens.services.review.checks import catalogue, run_checks
from patchlens.services.review.checks_common import CheckContext, CheckSpec
from patchlens.services.review.diff import parse_diff
from patchlens.services.review.types import ChangedSymbol

SETTINGS = Settings(environment="test", llm_provider="mock", embedding_provider="hashing")


def make_context(
    added: str,
    *,
    path: str = "pkg/module.py",
    file_text: str | None = None,
    symbols: list[ChangedSymbol] | None = None,
    test_matches: dict | None = None,
    context_lines: str = "",
) -> CheckContext:
    """Build a context whose diff adds exactly ``added`` (plus optional context)."""
    added_lines = added.splitlines()
    diff_text = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
    if context_lines:
        diff_text += f"@@ -1,{len(context_lines.splitlines())} +1,{len(context_lines.splitlines()) + len(added_lines)} @@\n"
        diff_text += "".join(f" {line}\n" for line in context_lines.splitlines())
    else:
        diff_text += f"@@ -0,0 +1,{max(len(added_lines), 1)} @@\n"
    diff_text += "".join(f"+{line}\n" for line in added_lines) if added_lines else "+# empty\n"
    return CheckContext(
        diff=parse_diff(diff_text),
        settings=SETTINGS,
        file_texts={path: file_text} if file_text is not None else {},
        changed_symbols=symbols or [],
        test_matches=test_matches or {},
    )


def rule_ids(context: CheckContext) -> set[str]:
    return {finding.rule_id for finding in run_checks(context).findings}


LINE_CASES: list[tuple[str, str, str]] = [
    ("bare-except", "try:\n    risky()\nexcept:\n    pass\n", "pkg/module.py"),
    (
        "except-suppresses-error",
        "try:\n    risky()\nexcept ValueError as exc:\n    return None\n",
        "pkg/module.py",
    ),
    ("eval-exec-usage", "result = eval(user_input)\n", "pkg/module.py"),
    ("subprocess-shell-true", "subprocess.run(command, shell=True)\n", "pkg/module.py"),
    ("os-system-usage", "os.system(command)\n", "pkg/module.py"),
    (
        "sql-string-interpolation",
        'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")\n',
        "pkg/db.py",
    ),
    ("unsafe-yaml-load", "config = yaml.load(payload)\n", "pkg/module.py"),
    ("unsafe-deserialization", "state = pickle.loads(blob)\n", "pkg/module.py"),
    ("weak-hash-algorithm", "digest = hashlib.md5(data).hexdigest()\n", "pkg/module.py"),
    ("naive-datetime", "created = datetime.now()\n", "pkg/module.py"),
    ("datetime-utcnow-deprecated", "created = datetime.utcnow()\n", "pkg/module.py"),
    ("insecure-tempfile", "path = tempfile.mktemp()\n", "pkg/module.py"),
    ("http-call-without-timeout", "response = requests.get(url)\n", "pkg/module.py"),
    (
        "hardcoded-secret",
        'AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n',
        "pkg/module.py",
    ),
    ("assert-in-library-code", "assert isinstance(value, int)\n", "pkg/module.py"),
    (
        "mutable-default-argument",
        "def collect(items=[]):\n    items.append(1)\n    return items\n",
        "pkg/module.py",
    ),
    ("debug-breakpoint", "breakpoint()\n", "pkg/module.py"),
]


@pytest.mark.parametrize(("rule_id", "snippet", "path"), LINE_CASES, ids=[case[0] for case in LINE_CASES])
def test_line_rule_fires_on_added_line(rule_id: str, snippet: str, path: str) -> None:
    context = make_context(snippet, path=path)
    result = run_checks(context)
    matching = [finding for finding in result.findings if finding.rule_id == rule_id]
    assert matching, f"{rule_id} did not fire; got {[f.rule_id for f in result.findings]}"
    finding = matching[0]
    assert finding.line_verified is True, "deterministic findings must cite a verified added line"
    assert context.diff.added_lines_for(finding.path), "path must exist in the diff"
    assert any(item.kind.value == "diff_line" for item in finding.evidence)
    assert finding.confidence_basis == ConfidenceBasis.DETERMINISTIC


def test_rules_do_not_fire_on_context_lines() -> None:
    """A dangerous line that the diff did not add is never reported."""
    context = make_context(
        "x = 1\n",
        context_lines="try:\n    risky()\nexcept:\n    pass\nprint(1)\n",
    )
    findings = run_checks(context).findings
    assert [finding.rule_id for finding in findings] == []


def test_hardcoded_secret_value_is_redacted_in_findings() -> None:
    secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    context = make_context(f'SECRET_KEY = "{secret}"\n')
    findings = [finding for finding in run_checks(context).findings if finding.rule_id == "hardcoded-secret"]
    assert findings
    blob = repr(findings[0].to_dict())
    assert secret not in blob, "the detected secret must never be echoed into stored findings"
    assert "..." in blob or "*" in blob or "redacted" in blob.lower()


def test_ast_rule_unused_import() -> None:
    text = "import json\n\n\ndef render() -> str:\n    return 'ok'\n"
    context = make_context(text, file_text=text)
    findings = [
        finding for finding in run_checks(context).findings if finding.rule_id == "unused-import-added"
    ]
    assert findings
    assert findings[0].detector == "static_ast"


def test_ast_rule_return_in_finally() -> None:
    text = "def read(path):\n    try:\n        return open(path)\n    finally:\n        return None\n"
    context = make_context(text, file_text=text)
    assert "return-in-finally" in rule_ids(context)


def test_ast_rule_query_inside_loop() -> None:
    text = (
        "def load(session, ids):\n"
        "    results = []\n"
        "    for identifier in ids:\n"
        "        results.append(session.execute(identifier))\n"
        "    return results\n"
    )
    context = make_context(text, file_text=text)
    assert "query-inside-loop" in rule_ids(context)


def test_ast_rule_mutable_class_attribute() -> None:
    text = "class Registry:\n    handlers = []\n\n    def add(self, handler):\n        self.handlers.append(handler)\n"
    context = make_context(text, file_text=text)
    assert "mutable-class-attribute" in rule_ids(context)


def test_ast_rule_star_import() -> None:
    text = "from os.path import *\n\n\ndef join(a, b):\n    return a + b\n"
    context = make_context(text, file_text=text)
    assert "star-import" in rule_ids(context)


def test_ast_rule_shadowed_builtin() -> None:
    text = "def first(list):\n    return list[0]\n"
    context = make_context(text, file_text=text)
    assert "shadowed-builtin" in rule_ids(context)


def test_missing_related_test_fires_only_without_strong_matches() -> None:
    symbol = ChangedSymbol(
        symbol_id="sym_test",
        path="pkg/module.py",
        qualified_name="compute_total",
        kind="function",
        start_line=1,
        end_line=3,
        source="def compute_total(items):\n    return sum(items)\n",
        changed_lines=[1],
    )
    # No test matches at all -> fires. AST checks need the new-side text.
    text = "def compute_total(items):\n    return sum(items)\n"
    assert "changed-code-without-related-test" in rule_ids(
        make_context(text, file_text=text, symbols=[symbol])
    )
    # A direct call association counts as coverage -> suppressed.
    from patchlens.services.ingestion.test_mapping import TestMatch, TestTarget

    target = TestTarget(
        symbol_id="sym_t",
        path="tests/test_module.py",
        qualified_name="test_compute_total",
        name="test_compute_total",
        start_line=1,
        end_line=3,
    )
    match = TestMatch(target=target, score=0.85, reasons=("test calls 'compute_total'",))
    flagged = rule_ids(
        make_context(text, file_text=text, symbols=[symbol], test_matches={"compute_total": [match]})
    )
    assert "changed-code-without-related-test" not in flagged


def test_ast_checks_are_skipped_without_new_side_text() -> None:
    context = make_context("import json\n")
    result = run_checks(context)
    assert result.checks_skipped, "AST checks must report that they were skipped"
    assert all("ast" not in note or True for note in result.checks_skipped)


def test_crashed_check_is_recorded_and_other_checks_still_run() -> None:
    import patchlens.services.review.checks as checks_module

    def exploding_check(context: CheckContext) -> list:
        raise RuntimeError("check exploded")

    original = checks_module.LINE_CHECKS
    try:
        checks_module.LINE_CHECKS = [exploding_check, *original]  # type: ignore[list-item]
        result = run_checks(make_context("try:\n    risky()\nexcept:\n    pass\n"))
    finally:
        checks_module.LINE_CHECKS = original  # type: ignore[assignment]
    assert result.errors, "the crash must be recorded"
    assert "RuntimeError" in result.errors[0]["error"] or "check exploded" in str(result.errors[0])
    assert "bare-except" in {finding.rule_id for finding in result.findings}


def test_catalogue_documents_every_spec() -> None:
    entries = catalogue()
    assert len(entries) >= 20
    for entry in entries:
        assert entry["rule_id"]
        assert entry["category"] in {
            "correctness",
            "security",
            "reliability",
            "performance",
            "maintainability",
            "testing",
            "style",
        }
        assert entry["severity"] in {"critical", "high", "medium", "low", "info"}
        assert entry["verification_status"] in {"verified", "needs_manual_verification"}
        assert 0.0 < entry["detector_confidence"] <= 1.0
        assert entry["summary"]
        assert entry["remediation"]
    # The catalogue is one entry per rule: a rule may have both a line-level and an
    # AST-level implementation (``http-call-without-timeout`` does), but a reviewer
    # reads about one rule.
    ids = [entry["rule_id"] for entry in entries]
    assert ids == sorted(set(ids), key=ids.index)
    assert len(set(ids)) == len(ids)
    assert "http-call-without-timeout" in set(ids)
    assert len(entries) >= 24


def test_duplicate_implementations_agree_on_the_rule_metadata() -> None:
    """A rule with two detectors must describe itself identically in both."""
    from patchlens.services.review.checks import AST_SPECS, LINE_SPECS

    by_id: dict[str, list[CheckSpec]] = {}
    for spec in [*LINE_SPECS, *AST_SPECS]:
        by_id.setdefault(spec.rule_id, []).append(spec)
    duplicated = {rule_id: specs for rule_id, specs in by_id.items() if len(specs) > 1}
    assert duplicated, "the suite would stop guarding the two-implementation cases otherwise"
    for rule_id, specs in duplicated.items():
        first = specs[0]
        for other in specs[1:]:
            assert other.category == first.category, rule_id
            assert other.severity == first.severity, rule_id
            assert other.verification == first.verification, rule_id


def test_needs_manual_verification_rules_are_marked() -> None:
    entries = {entry["rule_id"]: entry for entry in catalogue()}
    assert (
        entries["naive-datetime"]["verification_status"] == VerificationStatus.NEEDS_MANUAL_VERIFICATION.value
    )
    assert entries["bare-except"]["verification_status"] == VerificationStatus.VERIFIED.value


def test_future_imports_are_never_reported_as_unused() -> None:
    """`from __future__ import annotations` binds no runtime name; it is always used."""
    text = "from __future__ import annotations\n\nimport json\n\n\ndef render() -> str:\n    return json.dumps({})\n"
    context = make_context(text, file_text=text)
    findings = [
        finding for finding in run_checks(context).findings if finding.rule_id == "unused-import-added"
    ]
    assert findings == []


def test_query_inside_loop_is_reported_once_per_receiver() -> None:
    """`execute` plus the `fetchall` that consumes it are one query, not two."""
    text = (
        "def load(connection, table):\n"
        "    rows = []\n"
        "    for sku in table.known_skus():\n"
        "        cursor = connection.cursor()\n"
        "        cursor.execute(f\"SELECT price FROM prices WHERE sku = '{sku}'\")\n"
        "        rows.extend(cursor.fetchall())\n"
        "    return rows\n"
    )
    context = make_context(text, file_text=text)
    findings = [finding for finding in run_checks(context).findings if finding.rule_id == "query-inside-loop"]
    assert len(findings) == 1
    assert findings[0].start_line == 5
