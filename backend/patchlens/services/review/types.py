"""Shared review-pipeline types.

Kept free of database imports so checks, prompting and validation can be tested
in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from patchlens.core.enums import (
    ConfidenceBasis,
    EvidenceKind,
    FindingCategory,
    Severity,
    VerificationStatus,
)

SEVERITY_RANK: dict[str, int] = {
    Severity.CRITICAL.value: 4,
    Severity.HIGH.value: 3,
    Severity.MEDIUM.value: 2,
    Severity.LOW.value: 1,
    Severity.INFO.value: 0,
}


def severity_rank(severity: str) -> int:
    return SEVERITY_RANK.get(severity, 2)


@dataclass(slots=True)
class ChangedSymbol:
    """A symbol from the indexed repository that this diff touches."""

    symbol_id: str | None
    path: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    source: str
    signature: str | None = None
    docstring: str | None = None
    is_test: bool = False
    source_truncated: bool = False
    changed_lines: list[int] = field(default_factory=list)
    detection: str = "overlap"

    @property
    def added_line_count(self) -> int:
        return len(self.changed_lines)

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "signature": self.signature,
            "docstring": self.docstring,
            "is_test": self.is_test,
            "source": self.source,
            "changed_lines": self.changed_lines,
        }


@dataclass(slots=True)
class Evidence:
    """One verifiable support item for a finding."""

    kind: EvidenceKind
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        # ``kind`` is written last: detail payloads also carry a ``kind`` field
        # (the symbol's kind, for example) and must not overwrite the discriminator.
        return {**self.detail, "kind": self.kind.value}


@dataclass(slots=True)
class FindingDraft:
    """A candidate finding before persistence.

    ``verification_status`` is decided by the producer, never by a downstream
    score: deterministic checks set ``verified``/``needs_manual_verification``,
    models get ``ai_suggested``.
    """

    rule_id: str
    category: FindingCategory | str
    severity: Severity | str
    title: str
    explanation: str
    path: str
    start_line: int
    end_line: int
    evidence: list[Evidence] = field(default_factory=list)
    suggestion: str | None = None
    suggested_test: str | None = None
    verification_status: VerificationStatus | str = VerificationStatus.AI_SUGGESTED
    confidence: float | None = None
    confidence_basis: ConfidenceBasis | str = ConfidenceBasis.UNAVAILABLE
    detector: str = "static"
    provider: str | None = None
    model: str | None = None
    line_verified: bool = False
    rejection_reason: str | None = None
    dedupe_of: str | None = None

    @property
    def category_value(self) -> str:
        return self.category.value if isinstance(self.category, FindingCategory) else str(self.category)

    @property
    def severity_value(self) -> str:
        return self.severity.value if isinstance(self.severity, Severity) else str(self.severity)

    @property
    def verification_value(self) -> str:
        return (
            self.verification_status.value
            if isinstance(self.verification_status, VerificationStatus)
            else str(self.verification_status)
        )

    @property
    def confidence_basis_value(self) -> str:
        return (
            self.confidence_basis.value
            if isinstance(self.confidence_basis, ConfidenceBasis)
            else str(self.confidence_basis)
        )

    @property
    def rank(self) -> int:
        return severity_rank(self.severity_value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category_value,
            "severity": self.severity_value,
            "title": self.title,
            "explanation": self.explanation,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "evidence": [item.to_dict() for item in self.evidence],
            "suggestion": self.suggestion,
            "suggested_test": self.suggested_test,
            "verification_status": self.verification_value,
            "confidence": self.confidence,
            "confidence_basis": self.confidence_basis_value,
            "detector": self.detector,
            "provider": self.provider,
            "model": self.model,
            "line_verified": self.line_verified,
            "rejection_reason": self.rejection_reason,
        }


__all__ = ["SEVERITY_RANK", "ChangedSymbol", "Evidence", "FindingDraft", "severity_rank"]
