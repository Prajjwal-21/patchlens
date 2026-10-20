"""Shared machinery for deterministic review checks.

A deterministic check inspects the *added* lines of a diff (and, when the new file
content is available, the parsed AST) and emits :class:`FindingDraft` objects whose
source locations come from the diff itself.

Confidence semantics: deterministic detectors report a **fixed, hand-set** detector
confidence with basis ``deterministic_detector``. That number describes how
reliably the matched pattern implies a defect for that rule - it is not a
calibrated probability and is never learned from data. Checks whose *impact*
requires human judgement are marked ``needs_manual_verification`` rather than
``verified``.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from patchlens.core.config import Settings
from patchlens.core.enums import (
    ConfidenceBasis,
    EvidenceKind,
    FindingCategory,
    Severity,
    VerificationStatus,
)
from patchlens.services.ingestion.test_mapping import TestMatch
from patchlens.services.review.diff import ParsedDiff
from patchlens.services.review.types import ChangedSymbol, Evidence, FindingDraft


@dataclass(frozen=True, slots=True)
class CheckSpec:
    rule_id: str
    category: FindingCategory
    severity: Severity
    verification: VerificationStatus
    detector_confidence: float
    summary: str
    remediation: str

    def describe(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "verification_status": self.verification.value,
            "detector_confidence": self.detector_confidence,
            "summary": self.summary,
            "remediation": self.remediation,
        }


@dataclass(slots=True)
class CheckContext:
    """Everything a deterministic check may look at."""

    diff: ParsedDiff
    settings: Settings
    #: New-side file content for changed files we were able to read.
    file_texts: dict[str, str] = field(default_factory=dict)
    changed_symbols: list[ChangedSymbol] = field(default_factory=list)
    #: ``qualified_name -> ranked related tests`` (from test-to-source mapping).
    test_matches: dict[str, list[TestMatch]] = field(default_factory=dict)

    def added_lines(self, path: str | None = None) -> Iterator[tuple[str, int, str]]:
        """Yield ``(path, new_line_number, content)`` for added lines."""
        for file in self.diff.files:
            file_path = file.display_path
            if path is not None and file_path != path:
                continue
            for hunk in file.hunks:
                for line in hunk.lines:
                    if line.is_added and line.new_line is not None:
                        yield file_path, line.new_line, line.content

    def ast_for(self, path: str) -> ast.Module | None:
        text = self.file_texts.get(path)
        if text is None:
            return None
        try:
            return ast.parse(text, filename=path)
        except (SyntaxError, ValueError):
            return None

    def is_test_path(self, path: str) -> bool:
        from patchlens.services.ingestion.discovery import is_test_path

        return is_test_path(path)

    def added_line_numbers(self, path: str) -> set[int]:
        return self.diff.added_lines_for(path)


CheckFunction = Callable[[CheckContext], list[FindingDraft]]


def build_finding(
    spec: CheckSpec,
    *,
    path: str,
    start_line: int,
    end_line: int | None = None,
    title: str,
    explanation: str,
    evidence: list[Evidence],
    suggestion: str | None = None,
    suggested_test: str | None = None,
) -> FindingDraft:
    """Create a draft with the check's fixed detector confidence applied."""
    return FindingDraft(
        rule_id=spec.rule_id,
        category=spec.category,
        severity=spec.severity,
        title=title,
        explanation=explanation,
        path=path,
        start_line=start_line,
        end_line=end_line if end_line is not None else start_line,
        evidence=evidence,
        suggestion=suggestion or spec.remediation,
        suggested_test=suggested_test,
        verification_status=spec.verification,
        confidence=spec.detector_confidence,
        confidence_basis=ConfidenceBasis.DETERMINISTIC,
        detector="static",
    )


def line_evidence(path: str, line_number: int, content: str) -> Evidence:
    return Evidence(
        kind=EvidenceKind.DIFF_LINE,
        detail={"path": path, "line": line_number, "excerpt": content.strip()[:240]},
    )


def symbol_evidence(symbol: ChangedSymbol) -> Evidence:
    return Evidence(
        kind=EvidenceKind.SYMBOL,
        detail={
            "path": symbol.path,
            "qualified_name": symbol.qualified_name,
            "kind": symbol.kind,
            "start_line": symbol.start_line,
            "end_line": symbol.end_line,
            "signature": symbol.signature,
        },
    )


def call_name(node: ast.Call) -> str:
    """Best-effort dotted name of a call target."""
    target = node.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        parts: list[str] = [target.attr]
        current: ast.expr = target.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    try:
        return ast.unparse(target)[:120]
    except Exception:  # pragma: no cover - unparse is tolerant
        return "<unknown>"


def keyword_map(node: ast.Call) -> dict[str, ast.expr | None]:
    return {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip())


__all__ = [
    "CheckContext",
    "CheckFunction",
    "CheckSpec",
    "build_finding",
    "call_name",
    "indentation",
    "keyword_map",
    "line_evidence",
    "symbol_evidence",
]
