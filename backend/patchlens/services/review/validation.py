"""Validation of model output.

A model response is a *claim*, not a finding. This module is the boundary where a
claim becomes a stored finding, and it only lets through items that can be tied to
the reviewed diff:

* schema validation (Pydantic) for every item;
* the cited path must be a file this diff changes;
* the cited start line must be an **added** line on the new side of that diff;
* an end line must exist within the new file;
* an evidence excerpt, if supplied, must match the cited line;
* duplicates of deterministic findings and of earlier model findings are rejected;
* prompt-injection text echoed from repository content is rejected;
* confidence stays marked ``model_self_reported`` - never presented as calibrated.

Rejected items are returned too. They are persisted with ``verification_status =
rejected`` so a reviewer (and the evaluation harness) can audit what the model
tried to say, instead of it silently disappearing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from patchlens.core.enums import (
    ConfidenceBasis,
    EvidenceKind,
    FindingCategory,
    Severity,
    VerificationStatus,
)
from patchlens.core.security import contains_prompt_injection, content_fingerprint
from patchlens.services.llm.base import LLMCompletion
from patchlens.services.review.diff import ParsedDiff
from patchlens.services.review.types import ChangedSymbol, Evidence, FindingDraft, severity_rank

MIN_TITLE_CHARS = 8
MIN_EXPLANATION_CHARS = 24
MAX_TITLE_CHARS = 200
MAX_EXPLANATION_CHARS = 4_000
MAX_REJECTED_RETAINED = 100

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL | re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class RawFinding(BaseModel):
    """One item as returned by a model (schema-validated)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    rule_id: str | None = Field(default=None, max_length=80)
    category: FindingCategory
    severity: Severity
    title: str = Field(min_length=1, max_length=500)
    explanation: str = Field(min_length=1, max_length=8_000)
    path: str = Field(min_length=1, max_length=1_024)
    start_line: int = Field(ge=1)
    end_line: int | None = Field(default=None, ge=1)
    suggestion: str | None = Field(default=None, max_length=4_000)
    suggested_test: str | None = Field(default=None, max_length=4_000)
    evidence_excerpt: str | None = Field(default=None, max_length=1_000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class RawReview(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    findings: list[RawFinding] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=4_000)


@dataclass(slots=True)
class ValidationResult:
    accepted: list[FindingDraft] = field(default_factory=list)
    rejected: list[FindingDraft] = field(default_factory=list)
    notes: str | None = None
    parse_error: str | None = None
    supplied_count: int = 0
    deducted_duplicates: int = 0

    @property
    def all_drafts(self) -> list[FindingDraft]:
        return [*self.accepted, *self.rejected]

    def to_dict(self) -> dict[str, Any]:
        reasons: dict[str, int] = {}
        for draft in self.rejected:
            key = draft.rejection_reason or "unknown"
            reasons[key] = reasons.get(key, 0) + 1
        return {
            "supplied": self.supplied_count,
            "accepted": len(self.accepted),
            "rejected": len(self.rejected),
            "rejection_reasons": reasons,
            "deduplicated": self.deducted_duplicates,
            "parse_error": self.parse_error,
        }


def extract_payload(completion: LLMCompletion) -> tuple[dict[str, Any] | None, str | None]:
    """Get a JSON object from a completion, tolerating fences and prose."""
    if isinstance(completion.parsed, dict):
        return completion.parsed, None
    text = (completion.text or "").strip()
    if not text:
        return None, "provider returned no text and no parsed payload"
    fenced = _JSON_FENCE_RE.search(text)
    candidate = fenced.group("body").strip() if fenced else text
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            return None, "response was not JSON and contained no JSON object"
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            return None, f"could not parse JSON from response: {exc.msg} at character {exc.pos}"
    if not isinstance(parsed, dict):
        return None, "response JSON was not an object"
    return parsed, None


def validate_completion(
    completion: LLMCompletion,
    *,
    diff: ParsedDiff,
    deterministic_findings: list[FindingDraft],
    changed_symbols: list[ChangedSymbol] | None = None,
    file_texts: dict[str, str] | None = None,
    max_findings: int = 40,
) -> ValidationResult:
    """Turn a provider completion into accepted and rejected finding drafts."""
    result = ValidationResult()
    payload, error = extract_payload(completion)
    if payload is None:
        result.parse_error = error
        return result
    try:
        review = RawReview.model_validate(payload)
    except PydanticValidationError as exc:
        result.parse_error = _summarise_validation_error(exc)
        return result

    result.notes = review.notes
    result.supplied_count = len(review.findings)
    accepted: list[FindingDraft] = []
    rejected: list[FindingDraft] = []
    symbols = changed_symbols or []
    file_texts = file_texts or {}
    deterministic_index = _DetDuplicateIndex(deterministic_findings)
    seen: list[FindingDraft] = []

    for index, item in enumerate(review.findings):
        draft, reason = _validate_item(
            item,
            index=index,
            completion=completion,
            diff=diff,
            symbols=symbols,
            file_texts=file_texts,
        )
        if draft is None:  # unreadable/unknown file: nothing verifiable to retain
            continue
        if reason is not None:
            draft.verification_status = VerificationStatus.REJECTED
            draft.rejection_reason = reason
            if len(rejected) < MAX_REJECTED_RETAINED:
                rejected.append(draft)
            continue
        duplicate_of = deterministic_index.match(draft)
        if duplicate_of is not None:
            draft.verification_status = VerificationStatus.REJECTED
            draft.rejection_reason = f"duplicate_of_deterministic:{duplicate_of.rule_id}"
            draft.dedupe_of = duplicate_of.rule_id
            result.deducted_duplicates += 1
            if len(rejected) < MAX_REJECTED_RETAINED:
                rejected.append(draft)
            continue
        duplicate = next((existing for existing in seen if _same_issue(existing, draft)), None)
        if duplicate is not None:
            draft.verification_status = VerificationStatus.REJECTED
            draft.rejection_reason = f"duplicate_of_model_finding:{duplicate.title}"
            draft.dedupe_of = duplicate.rule_id
            result.deducted_duplicates += 1
            if len(rejected) < MAX_REJECTED_RETAINED:
                rejected.append(draft)
            continue
        seen.append(draft)
        accepted.append(draft)

    accepted.sort(key=lambda item: (-severity_rank(item.severity_value), item.path, item.start_line))
    for extra in accepted[max_findings:]:
        extra.verification_status = VerificationStatus.REJECTED
        extra.rejection_reason = "limit_exceeded"
        if len(rejected) < MAX_REJECTED_RETAINED:
            rejected.append(extra)
    result.accepted = accepted[:max_findings]
    result.rejected = rejected
    return result


def dedupe_drafts(drafts: list[FindingDraft]) -> list[FindingDraft]:
    """De-duplicate producer drafts on ``(rule_id, path, overlapping line)``."""
    unique: list[FindingDraft] = []
    for draft in drafts:
        if any(_same_rule_overlap(existing, draft) for existing in unique):
            continue
        unique.append(draft)
    return unique


def finding_fingerprint(finding: FindingDraft) -> str:
    """Stable identity for the unique constraint on ``findings``."""
    return content_fingerprint(
        finding.rule_id,
        finding.path,
        str(finding.start_line),
        normalise_text(finding.title).lower(),
        length=64,
    )


def normalise_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def normalise_path(candidate: str) -> str:
    """Strip git-style prefixes until the path is stable (``a/./x.py`` -> ``x.py``)."""
    path = candidate.strip().replace("\\", "/")
    changed = True
    while changed:
        changed = False
        for prefix in ("./", "a/", "b/"):
            if path.startswith(prefix):
                path = path[len(prefix) :]
                changed = True
    return path.lstrip("/")


def _validate_item(
    item: RawFinding,
    *,
    index: int,
    completion: LLMCompletion,
    diff: ParsedDiff,
    symbols: list[ChangedSymbol],
    file_texts: dict[str, str],
) -> tuple[FindingDraft | None, str | None]:
    path = normalise_path(item.path)
    file = diff.file_for(path)
    if file is None:
        return None, None  # unknown path: no location to retain, nothing to audit
    if file.status == "deleted" or file.is_binary:
        return _candidate(item, path=path, completion=completion, rule_suffix=index), "file_not_reviewable"
    added = diff.added_lines_for(path)
    if not added:
        return _candidate(
            item, path=path, completion=completion, rule_suffix=index
        ), "file_has_no_added_lines"

    start = item.start_line
    if start not in added:
        return (
            _candidate(item, path=path, completion=completion, rule_suffix=index),
            f"line_not_added_in_diff:{start}",
        )
    max_line = _max_new_line(file, file_texts.get(path))
    end = item.end_line if item.end_line is not None else start
    if end < start:
        return _candidate(item, path=path, completion=completion, rule_suffix=index), "invalid_range"
    if max_line is not None and end > max_line:
        return _candidate(item, path=path, completion=completion, rule_suffix=index), "end_line_out_of_bounds"

    actual_line = diff.added_line_text(path, start) or ""
    if item.evidence_excerpt and not _excerpt_matches(item.evidence_excerpt, actual_line):
        return (
            _candidate(item, path=path, completion=completion, rule_suffix=index),
            "evidence_excerpt_does_not_match_cited_line",
        )

    title = _sanitise(item.title, MAX_TITLE_CHARS)
    explanation = _sanitise(item.explanation, MAX_EXPLANATION_CHARS)
    if len(title) < MIN_TITLE_CHARS:
        return _candidate(item, path=path, completion=completion, rule_suffix=index), "title_too_short"
    if len(explanation) < MIN_EXPLANATION_CHARS:
        return _candidate(item, path=path, completion=completion, rule_suffix=index), "explanation_too_short"
    if contains_prompt_injection(title) or contains_prompt_injection(explanation):
        return (
            _candidate(item, path=path, completion=completion, rule_suffix=index),
            "prompt_injection_content",
        )

    evidence = [
        Evidence(
            kind=EvidenceKind.DIFF_LINE,
            detail={"path": path, "line": start, "excerpt": actual_line.strip()[:240]},
        )
    ]
    symbol = _symbol_at(symbols, path, start)
    if symbol is not None:
        evidence.append(
            Evidence(
                kind=EvidenceKind.SYMBOL,
                detail={
                    "path": symbol.path,
                    "qualified_name": symbol.qualified_name,
                    "start_line": symbol.start_line,
                    "end_line": symbol.end_line,
                    "kind": symbol.kind,
                },
            )
        )
    evidence.append(
        Evidence(
            kind=EvidenceKind.MODEL,
            detail={
                "provider": completion.provider,
                "model": completion.model,
                "is_mock": completion.is_mock,
                "finish_reason": completion.finish_reason,
            },
        )
    )
    rule_id = _rule_id(item, index)
    draft = FindingDraft(
        rule_id=rule_id,
        category=item.category.value,
        severity=item.severity.value,
        title=title,
        explanation=explanation,
        path=path,
        start_line=start,
        end_line=end,
        evidence=evidence,
        suggestion=_sanitise(item.suggestion, MAX_EXPLANATION_CHARS) if item.suggestion else None,
        suggested_test=_sanitise(item.suggested_test, MAX_EXPLANATION_CHARS) if item.suggested_test else None,
        verification_status=VerificationStatus.AI_SUGGESTED,
        confidence=item.confidence,
        confidence_basis=(
            ConfidenceBasis.MODEL_SELF_REPORTED
            if item.confidence is not None
            else ConfidenceBasis.UNAVAILABLE
        ),
        detector="llm",
        provider=completion.provider,
        model=completion.model,
        line_verified=True,
    )
    return draft, None


def _candidate(item: RawFinding, *, path: str, completion: LLMCompletion, rule_suffix: int) -> FindingDraft:
    return FindingDraft(
        rule_id=_rule_id(item, rule_suffix),
        category=item.category.value,
        severity=item.severity.value,
        title=_sanitise(item.title, MAX_TITLE_CHARS),
        explanation=_sanitise(item.explanation, MAX_EXPLANATION_CHARS),
        path=path,
        start_line=item.start_line,
        end_line=max(item.start_line, item.end_line or item.start_line),
        evidence=[
            Evidence(
                kind=EvidenceKind.MODEL,
                detail={
                    "provider": completion.provider,
                    "model": completion.model,
                    "is_mock": completion.is_mock,
                },
            )
        ],
        verification_status=VerificationStatus.REJECTED,
        confidence=item.confidence,
        confidence_basis=(
            ConfidenceBasis.MODEL_SELF_REPORTED
            if item.confidence is not None
            else ConfidenceBasis.UNAVAILABLE
        ),
        detector="llm",
        provider=completion.provider,
        model=completion.model,
        line_verified=False,
    )


def _rule_id(item: RawFinding, index: int) -> str:
    if item.rule_id:
        slug = re.sub(r"[^a-z0-9_.-]+", "-", item.rule_id.lower()).strip("-")
        if slug:
            return slug[:60]
    slug = re.sub(r"[^a-z0-9]+", "-", item.title.lower()).strip("-")[:40]
    return f"model-{slug or 'finding'}-{index}"


def _max_new_line(file: Any, text: str | None) -> int | None:
    if text is not None:
        return max(1, len(text.splitlines()))
    lines = [line.new_line for hunk in file.hunks for line in hunk.lines if line.new_line is not None]
    return max(lines) if lines else None


def _excerpt_matches(supplied: str, actual: str) -> bool:
    def collapse(value: str) -> str:
        return _WHITESPACE_RE.sub("", value).strip("\"'`")

    expected = collapse(supplied)
    real = collapse(actual)
    if not expected or not real:
        return False
    return expected in real or real in expected


def _sanitise(value: str | None, limit: int) -> str:
    if not value:
        return ""
    return _CONTROL_RE.sub("", normalise_text(value))[:limit]


def _symbol_at(symbols: list[ChangedSymbol], path: str, line: int) -> ChangedSymbol | None:
    for symbol in symbols:
        if symbol.path == path and symbol.start_line <= line <= symbol.end_line:
            return symbol
    return None


def _same_rule_overlap(left: FindingDraft, right: FindingDraft) -> bool:
    return left.rule_id == right.rule_id and left.path == right.path and _overlaps(left, right)


def _overlaps(left: FindingDraft, right: FindingDraft) -> bool:
    return left.start_line <= right.end_line and right.start_line <= left.end_line


def _same_issue(left: FindingDraft, right: FindingDraft) -> bool:
    """Two model findings are the same issue only if they say the same thing.

    Sharing a category and a line is not enough: a debug `print` and an unresolved
    `TODO` can legitimately sit on the same added line and both be worth reporting.
    """
    if left.path != right.path or not _overlaps(left, right):
        return False
    if left.rule_id == right.rule_id:
        return True
    left_title = normalise_text(left.title).lower()
    right_title = normalise_text(right.title).lower()
    if left_title == right_title:
        return True
    return SequenceMatcher(None, left_title, right_title).ratio() >= 0.75


class _DetDuplicateIndex:
    """Match model findings against deterministic findings for one diff."""

    def __init__(self, findings: list[FindingDraft]) -> None:
        self._findings = findings

    def match(self, draft: FindingDraft) -> FindingDraft | None:
        for finding in self._findings:
            if finding.path != draft.path:
                continue
            if not _overlaps(finding, draft):
                continue
            if finding.rule_id == draft.rule_id or finding.category_value == draft.category_value:
                return finding
        return None


def _summarise_validation_error(exc: PydanticValidationError) -> str:
    parts = []
    for error in exc.errors()[:5]:
        location = ".".join(str(part) for part in error.get("loc", ())) or "(root)"
        parts.append(f"{location}: {error.get('msg', 'invalid value')}")
    return "schema validation failed - " + "; ".join(parts)


__all__ = [
    "RawFinding",
    "RawReview",
    "ValidationResult",
    "dedupe_drafts",
    "extract_payload",
    "finding_fingerprint",
    "normalise_path",
    "normalise_text",
    "validate_completion",
]
