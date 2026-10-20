"""Deterministic check orchestration.

Runs the line checks (always) and AST checks (when the changed file's new content
was readable), collects findings, and records what ran. A crashing check is
recorded and skipped rather than aborting the review - but never silently.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from patchlens.core.logging import describe_exception, get_logger, log_extra
from patchlens.services.review.checks_ast import AST_CHECKS, AST_SPECS
from patchlens.services.review.checks_common import CheckContext, CheckFunction, CheckSpec
from patchlens.services.review.checks_line import LINE_CHECKS, LINE_SPECS
from patchlens.services.review.types import FindingDraft

logger = get_logger(__name__)


@dataclass(slots=True)
class CheckRunResult:
    findings: list[FindingDraft]
    checks_run: list[str] = field(default_factory=list)
    checks_skipped: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": len(self.findings),
            "checks_run": len(self.checks_run),
            "skipped": self.checks_skipped,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
        }


def catalogue() -> list[dict[str, object]]:
    """Machine-readable rule catalogue for the UI/docs.

    One entry per rule: a rule may have both a line-level and an AST-level
    implementation (``http-call-without-timeout`` does), which is a property of the
    detector, not of the rule the reviewer reads about.
    """
    specs: list[CheckSpec] = [*LINE_SPECS, *AST_SPECS]
    seen: set[str] = set()
    entries: list[dict[str, object]] = []
    for spec in specs:
        if spec.rule_id in seen:
            continue
        seen.add(spec.rule_id)
        entries.append(spec.describe())
    return entries


def run_checks(context: CheckContext) -> CheckRunResult:
    started = time.perf_counter()
    findings: list[FindingDraft] = []
    run: list[str] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    functions: list[tuple[CheckFunction, bool]] = [(function, False) for function in LINE_CHECKS]
    if context.file_texts:
        functions.extend((function, True) for function in AST_CHECKS)
    else:
        skipped.append(
            {
                "reason": "no new-side file content available",
                "detail": "AST checks need the changed file's post-change text; line checks still ran",
            }
        )

    for function, is_ast in functions:
        try:
            produced = function(context)
        except Exception as exc:
            detail = describe_exception(exc)
            errors.append({"check": function.__name__, "error": detail["type"], "message": detail["message"]})
            logger.warning("deterministic check failed", extra=log_extra(check=function.__name__, **detail))
            continue
        run.append(function.__name__)
        for finding in produced:
            finding.detector = "static" if not is_ast else "static_ast"
            findings.append(finding)

    added_by_path = {file.display_path: file.added_line_numbers for file in context.diff.files}
    for finding in findings:
        finding.line_verified = finding.start_line in added_by_path.get(finding.path, set())

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.debug(
        "deterministic checks finished",
        extra=log_extra(findings=len(findings), checks=len(run), duration_ms=duration_ms),
    )
    return CheckRunResult(
        findings=findings, checks_run=run, checks_skipped=skipped, errors=errors, duration_ms=duration_ms
    )


__all__ = ["CheckRunResult", "catalogue", "run_checks"]
