"""AST-backed checks.

These run only for changed files whose new-side content PatchLens could read (an
indexed checkout, a local demo repository, or an explicit file map). They are more
precise than the line scans: they can distinguish a mutable default *argument* from
a mutable class attribute, and find a missing `timeout=` across multiple lines.

Every finding is still restricted to lines that the diff actually added, so a check
can never flag pre-existing code as if the pull request introduced it.
"""

from __future__ import annotations

import ast

from patchlens.core.enums import FindingCategory, Severity, VerificationStatus
from patchlens.services.ingestion.test_mapping import has_plausible_coverage
from patchlens.services.review.checks_common import (
    CheckContext,
    CheckFunction,
    CheckSpec,
    build_finding,
    call_name,
    keyword_map,
    line_evidence,
    symbol_evidence,
)
from patchlens.services.review.types import ChangedSymbol, Evidence, FindingDraft

BUILTIN_NAMES = {
    "id",
    "list",
    "dict",
    "set",
    "tuple",
    "type",
    "input",
    "format",
    "filter",
    "map",
    "len",
    "sum",
    "min",
    "max",
    "open",
    "print",
    "hash",
    "object",
    "range",
    "str",
    "int",
    "float",
    "bool",
    "bytes",
    "vars",
    "next",
    "iter",
    "sorted",
    "any",
    "all",
}
DB_METHODS = {
    "execute",
    "executemany",
    "query",
    "filter",
    "filter_by",
    "get",
    "all",
    "first",
    "one",
    "fetchone",
    "fetchall",
    "find",
    "find_one",
    "objects",
    "scalar",
    "scalars",
    "count",
}
DB_RECEIVERS = {
    "session",
    "db",
    "conn",
    "connection",
    "cursor",
    "client",
    "repo",
    "repository",
    "orm",
    "query",
}
HTTP_MODULES = ("requests", "httpx", "aiohttp", "urllib3")

SPEC_UNUSED_IMPORT = CheckSpec(
    rule_id="unused-import-added",
    category=FindingCategory.MAINTAINABILITY,
    severity=Severity.LOW,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=0.9,
    summary="an import added by this change is never referenced",
    remediation="Remove the import, or reference it (and add `# noqa: F401` only for intentional re-exports).",
)

SPEC_RETURN_IN_FINALLY = CheckSpec(
    rule_id="return-in-finally",
    category=FindingCategory.CORRECTNESS,
    severity=Severity.HIGH,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=1.0,
    summary="a `return` inside `finally` discards in-flight exceptions",
    remediation="Move the return out of `finally`; use the `else` block for the success path.",
)

SPEC_N_PLUS_ONE = CheckSpec(
    rule_id="query-inside-loop",
    category=FindingCategory.PERFORMANCE,
    severity=Severity.MEDIUM,
    verification=VerificationStatus.NEEDS_MANUAL_VERIFICATION,
    detector_confidence=0.5,
    summary="a database call inside a loop scales linearly with the loop length",
    remediation="Fetch the needed rows in one query (eager loading / `IN (...)`) before the loop.",
)

SPEC_HTTP_TIMEOUT_AST = CheckSpec(
    rule_id="http-call-without-timeout",
    category=FindingCategory.RELIABILITY,
    severity=Severity.MEDIUM,
    verification=VerificationStatus.NEEDS_MANUAL_VERIFICATION,
    detector_confidence=0.7,
    summary="HTTP calls without a timeout can block a worker indefinitely",
    remediation="Pass an explicit `timeout=` (requests/httpx both default to unbounded waits).",
)

SPEC_MISSING_TEST = CheckSpec(
    rule_id="changed-code-without-related-test",
    category=FindingCategory.TESTING,
    severity=Severity.MEDIUM,
    # The association search itself is deterministic, but "no test covers this"
    # is a conclusion about code PatchLens cannot fully see (dynamic dispatch,
    # tests in other repositories, generated suites), so it asks for a human.
    verification=VerificationStatus.NEEDS_MANUAL_VERIFICATION,
    detector_confidence=0.6,
    summary="no existing test is structurally associated with the changed code",
    remediation="Add or extend a test that exercises the changed behaviour and would fail without the fix.",
)

SPEC_SHADOWED_BUILTIN = CheckSpec(
    rule_id="shadowed-builtin",
    category=FindingCategory.MAINTAINABILITY,
    severity=Severity.LOW,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=0.85,
    summary="a builtin name is shadowed, so later code cannot use the original",
    remediation="Rename the variable/parameter (e.g. `items` instead of `list`).",
)

SPEC_MUTABLE_CLASS_ATTR = CheckSpec(
    rule_id="mutable-class-attribute",
    category=FindingCategory.CORRECTNESS,
    severity=Severity.LOW,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=0.8,
    summary="mutable class attributes are shared by every instance",
    remediation="Initialise mutable state per instance in `__init__`.",
)

SPEC_STAR_IMPORT = CheckSpec(
    rule_id="star-import",
    category=FindingCategory.MAINTAINABILITY,
    severity=Severity.LOW,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=0.9,
    summary="`from module import *` hides what is actually used and can silently overwrite names",
    remediation="Import the specific names you need.",
)


def check_mutable_default_arguments(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path in context.file_texts:
        tree = context.ast_for(path)
        if tree is None:
            continue
        added = context.added_line_numbers(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            all_args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            defaults: list[ast.expr | None] = [None] * (len(all_args) - len(node.args.defaults))
            defaults.extend(node.args.defaults)
            if node.args.kw_defaults:
                kw_start = len(node.args.posonlyargs) + len(node.args.args)
                for offset, default in enumerate(node.args.kw_defaults):
                    defaults[kw_start + offset] = default
            for argument, default in zip(all_args, defaults, strict=False):
                if default is None or not _is_mutable_literal(default):
                    continue
                line = getattr(default, "lineno", node.lineno)
                if line not in added and node.lineno not in added:
                    continue
                findings.append(
                    build_finding(
                        _mutable_default_spec(),
                        path=path,
                        start_line=line,
                        title=f"Mutable default argument `{argument.arg}` in `{node.name}`",
                        explanation=(
                            f"`def {node.name}(...)` gives `{argument.arg}` a mutable default"
                            f" (`{_render(default)}`). Python evaluates defaults once at definition time, so"
                            " every call shares the same object and mutations leak between calls."
                        ),
                        evidence=[
                            line_evidence(
                                path,
                                line,
                                f"{argument.arg} = {_render(default)}",
                            )
                        ],
                        suggestion=(
                            f"Use `{argument.arg}: ... | None = None` and create the container inside the"
                            " function body."
                        ),
                    )
                )
    return findings


def _mutable_default_spec() -> CheckSpec:
    from patchlens.services.review.checks_line import SPEC_MUTABLE_DEFAULT

    return SPEC_MUTABLE_DEFAULT


def _is_mutable_literal(node: ast.expr) -> bool:
    if isinstance(node, ast.List | ast.Dict | ast.Set):
        return True
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"list", "dict", "set"}
    )


def _render(node: ast.expr) -> str:
    try:
        return ast.unparse(node)[:80]
    except Exception:  # pragma: no cover - unparse is tolerant
        return "?"


def check_unused_imports(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path in context.file_texts:
        tree = context.ast_for(path)
        if tree is None:
            continue
        added = context.added_line_numbers(path)
        used = _used_names(tree)
        declared = _declared_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            if node.lineno not in added:
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                # Future imports have no runtime binding at all: `annotations` and
                # friends can never be "used", so reporting them would be noise on
                # a line that is correct practice.
                continue
            if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
                findings.append(
                    build_finding(
                        SPEC_STAR_IMPORT,
                        path=path,
                        start_line=node.lineno,
                        title="Star import added",
                        explanation=(
                            f"`{SPEC_STAR_IMPORT.summary}`. The import on line {node.lineno} pulls every public"
                            " name from the module into this namespace."
                        ),
                        evidence=[line_evidence(path, node.lineno, f"from {node.module or ''} import *")],
                    )
                )
                continue
            for alias in node.names:
                bound = _bound_name(alias)
                if bound in used or bound in declared or bound == "*":
                    continue
                if bound.startswith("_"):
                    continue
                findings.append(
                    build_finding(
                        SPEC_UNUSED_IMPORT,
                        path=path,
                        start_line=node.lineno,
                        title=f"Unused import `{bound}`",
                        explanation=(
                            f"`{bound}` is imported on line {node.lineno} but never referenced in {path}. This is"
                            " a deterministic result for this file; re-exports via `__all__` and dynamic access"
                            " are handled, but `getattr(module, ...)`-style access cannot be."
                        ),
                        evidence=[line_evidence(path, node.lineno, f"import {bound}")],
                    )
                )
    return findings


def _bound_name(alias: ast.alias) -> str:
    if alias.asname:
        return alias.asname
    return alias.name.split(".")[0]


def _declared_names(tree: ast.Module) -> set[str]:
    """Names listed in ``__all__`` (string literals) count as intentional re-exports."""
    declared: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if "__all__" not in targets:
                continue
            for element in getattr(node.value, "elts", []):
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    declared.add(element.value)
    return declared


def _used_names(tree: ast.Module) -> set[str]:
    used: set[str] = set()
    import_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            import_nodes.add(id(node))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and id(node) not in import_nodes:
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            current: ast.expr = node
            while isinstance(current, ast.Attribute):
                current = current.value
            if isinstance(current, ast.Name):
                used.add(current.id)
    return used


def check_return_in_finally(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path in context.file_texts:
        tree = context.ast_for(path)
        if tree is None:
            continue
        added = context.added_line_numbers(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for statement in node.finalbody:
                if not isinstance(statement, ast.Return):
                    continue
                if statement.lineno not in added:
                    continue
                findings.append(
                    build_finding(
                        SPEC_RETURN_IN_FINALLY,
                        path=path,
                        start_line=statement.lineno,
                        title="`return` inside a `finally` block",
                        explanation=(
                            f"`{SPEC_RETURN_IN_FINALLY.summary}`. The `finally` block at line"
                            f" {statement.lineno} returns, so any exception raised in `try`/`except` is silently"
                            " discarded and the caller sees a normal result."
                        ),
                        evidence=[line_evidence(path, statement.lineno, "return ...  # inside finally")],
                    )
                )
    return findings


def check_query_in_loop(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    emitted: set[tuple[str, int, str]] = set()
    for path in context.file_texts:
        tree = context.ast_for(path)
        if tree is None:
            continue
        added = context.added_line_numbers(path)
        for loop in ast.walk(tree):
            if not isinstance(loop, ast.For | ast.AsyncFor | ast.While):
                continue
            # One finding per receiver per loop. `cursor.execute(...)` and the
            # `cursor.fetchall()` that consumes it are a single query, and a loop
            # nested in another loop must not report the same call twice.
            seen_receivers: set[str] = set()
            for statement in loop.body:
                for node in ast.walk(statement):
                    if not isinstance(node, ast.Call):
                        continue
                    name = call_name(node)
                    parts = name.split(".")
                    if len(parts) < 2 or parts[-1] not in DB_METHODS or parts[0] not in DB_RECEIVERS:
                        continue
                    if node.lineno not in added:
                        continue
                    receiver = ".".join(parts[:-1])
                    key = (path, node.lineno, receiver)
                    if receiver in seen_receivers or key in emitted:
                        continue
                    seen_receivers.add(receiver)
                    emitted.add(key)
                    findings.append(
                        build_finding(
                            SPEC_N_PLUS_ONE,
                            path=path,
                            start_line=node.lineno,
                            title=f"`{name}(...)` executed inside a loop",
                            explanation=(
                                f"`{SPEC_N_PLUS_ONE.summary}`. Line {node.lineno} issues a query per iteration"
                                " of the enclosing loop. It is only a defect if the loop is over a non-trivial"
                                " number of items, hence manual verification."
                            ),
                            evidence=[line_evidence(path, node.lineno, f"{name}(...)")],
                        )
                    )
    return findings


def check_http_calls_ast(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path in context.file_texts:
        tree = context.ast_for(path)
        if tree is None:
            continue
        added = context.added_line_numbers(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            if name.split(".")[0] not in HTTP_MODULES:
                continue
            if node.lineno not in added:
                continue
            keywords = keyword_map(node)
            if "timeout" in keywords and not (
                isinstance(keywords["timeout"], ast.Constant) and keywords["timeout"].value is None
            ):
                continue
            findings.append(
                build_finding(
                    SPEC_HTTP_TIMEOUT_AST,
                    path=path,
                    start_line=node.lineno,
                    title=f"`{name}(...)` without an explicit timeout",
                    explanation=(
                        f"`{SPEC_HTTP_TIMEOUT_AST.summary}`. The call on line {node.lineno} passes no"
                        " `timeout=`, so a stalled peer can hold this code path open indefinitely."
                    ),
                    evidence=[line_evidence(path, node.lineno, f"{name}(...)")],
                )
            )
    return findings


def _missing_test_evidence(context: CheckContext, symbol: ChangedSymbol, anchor: int) -> list[Evidence]:
    """Symbol identity plus, when the anchor is an added line, that diff line."""
    evidence = [symbol_evidence(symbol)]
    line_text = context.diff.added_line_text(symbol.path, anchor)
    if line_text is not None:
        evidence.append(line_evidence(symbol.path, anchor, line_text))
    return evidence


def check_missing_related_tests(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for symbol in context.changed_symbols:
        if symbol.is_test or symbol.kind not in {"function", "method", "class"}:
            continue
        matches = context.test_matches.get(symbol.qualified_name, [])
        if has_plausible_coverage(matches):
            continue
        anchor = min(symbol.changed_lines) if symbol.changed_lines else symbol.start_line
        findings.append(
            build_finding(
                SPEC_MISSING_TEST,
                path=symbol.path,
                start_line=anchor,
                end_line=anchor,
                title=f"No related test found for `{symbol.qualified_name}`",
                explanation=(
                    "PatchLens associates tests structurally (mirrored file names, imports, call names and"
                    f" shared identifiers). No indexed test matched `{symbol.qualified_name}` above the"
                    " association threshold. This does not prove the behaviour is untested (dynamic dispatch"
                    " defeats structural matching), but the review could not point at a test that would catch"
                    " a regression here."
                ),
                evidence=_missing_test_evidence(context, symbol, anchor),
                suggestion=(
                    "Add a focused test that fails without this change, ideally in a file that mirrors"
                    f" `{symbol.path}`."
                ),
            )
        )
    return findings


def check_shadowed_builtins(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path in context.file_texts:
        tree = context.ast_for(path)
        if tree is None:
            continue
        added = context.added_line_numbers(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and node.lineno in added:
                for target in node.targets:
                    for name_node in ast.walk(target):
                        if isinstance(name_node, ast.Name) and name_node.id in BUILTIN_NAMES:
                            findings.append(_shadow_finding(path, node.lineno, name_node.id))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                    if argument.arg in BUILTIN_NAMES and argument.lineno in added:
                        findings.append(_shadow_finding(path, argument.lineno, argument.arg))
    return findings


def _shadow_finding(path: str, line: int, name: str) -> FindingDraft:
    return build_finding(
        SPEC_SHADOWED_BUILTIN,
        path=path,
        start_line=line,
        title=f"Builtin name `{name}` shadowed",
        explanation=(
            f"`{SPEC_SHADOWED_BUILTIN.summary}`. Line {line} rebinds `{name}`, so any later use of the"
            " builtin in this scope resolves to the new value."
        ),
        evidence=[line_evidence(path, line, f"{name} = ...")],
    )


def check_mutable_class_attributes(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path in context.file_texts:
        tree = context.ast_for(path)
        if tree is None:
            continue
        added = context.added_line_numbers(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if not isinstance(statement, ast.Assign) or statement.lineno not in added:
                    continue
                if not _is_mutable_literal(statement.value):
                    continue
                names = ", ".join(target.id for target in statement.targets if isinstance(target, ast.Name))
                if not names:
                    continue
                findings.append(
                    build_finding(
                        SPEC_MUTABLE_CLASS_ATTR,
                        path=path,
                        start_line=statement.lineno,
                        title=f"Mutable class attribute `{names}` in `{node.name}`",
                        explanation=(
                            f"`{SPEC_MUTABLE_CLASS_ATTR.summary}`. `{names}` is created once on the class"
                            " object, so instances that mutate it affect each other."
                        ),
                        evidence=[
                            line_evidence(path, statement.lineno, f"{names} = {_render(statement.value)}")
                        ],
                    )
                )
    return findings


AST_CHECKS: list[CheckFunction] = [
    check_mutable_default_arguments,
    check_unused_imports,
    check_return_in_finally,
    check_query_in_loop,
    check_http_calls_ast,
    check_missing_related_tests,
    check_shadowed_builtins,
    check_mutable_class_attributes,
]

AST_SPECS: list[CheckSpec] = [
    SPEC_UNUSED_IMPORT,
    SPEC_RETURN_IN_FINALLY,
    SPEC_N_PLUS_ONE,
    SPEC_HTTP_TIMEOUT_AST,
    SPEC_MISSING_TEST,
    SPEC_SHADOWED_BUILTIN,
    SPEC_MUTABLE_CLASS_ATTR,
    SPEC_STAR_IMPORT,
]

__all__ = [
    "AST_CHECKS",
    "AST_SPECS",
    "SPEC_HTTP_TIMEOUT_AST",
    "SPEC_MISSING_TEST",
    "SPEC_MUTABLE_CLASS_ATTR",
    "SPEC_N_PLUS_ONE",
    "SPEC_RETURN_IN_FINALLY",
    "SPEC_SHADOWED_BUILTIN",
    "SPEC_STAR_IMPORT",
    "SPEC_UNUSED_IMPORT",
    "check_http_calls_ast",
    "check_missing_related_tests",
    "check_mutable_class_attributes",
    "check_mutable_default_arguments",
    "check_query_in_loop",
    "check_return_in_finally",
    "check_shadowed_builtins",
    "check_unused_imports",
]
