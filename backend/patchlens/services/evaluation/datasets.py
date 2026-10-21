"""Evaluation dataset schema, validation and seeding.

A dataset is a versioned JSON document. Its content hash is stored with every run,
so a number can always be traced back to the exact labels that produced it. Datasets
are immutable once seeded: changing the labels requires bumping ``version``.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from patchlens.core.enums import FindingCategory
from patchlens.core.security import is_safe_relative_path
from patchlens.db.models.evaluation import EvalCase, EvalDataset
from patchlens.services.review.diff import parse_diff


class DatasetError(ValueError):
    """The dataset document is invalid or inconsistent with the repository."""


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrievalLabel(_Base):
    """One graded relevant symbol for a retrieval query."""

    path: str
    qualified_name: str
    grade: Annotated[int, Field(ge=1, le=3)] = 1
    note: str | None = None


class RetrievalCase(_Base):
    id: str
    query: str
    relevant: list[RetrievalLabel]
    split: Literal["train", "dev", "test"] = "test"
    notes: str | None = None

    @field_validator("relevant")
    @classmethod
    def _at_least_one_label(cls, value: list[RetrievalLabel]) -> list[RetrievalLabel]:
        if not value:
            raise ValueError("a retrieval case needs at least one relevant symbol")
        keys = [(label.path, label.qualified_name) for label in value]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate relevant symbol in one case")
        return value


class ExpectedFinding(_Base):
    """A finding a human reviewer says the system should produce.

    ``must_find`` marks a label whose absence is a hard failure (a defect that must
    not be merged). Scoring weights every label equally; the flag drives the
    separate ``must_find`` recall so "missed a critical bug" cannot be hidden by a
    large number of optional labels. It defaults to ``False``: an author has to
    state that a label is mandatory.
    """

    category: FindingCategory
    path: str
    line: int = Field(ge=1)
    rule_id: str | None = None
    must_find: bool = False
    note: str | None = None


class ReviewCase(_Base):
    id: str
    diff_text: str
    commit_sha: str
    expected: list[ExpectedFinding]
    split: Literal["train", "dev", "test"] = "test"
    notes: str | None = None

    @model_validator(mode="after")
    def _labels_anchor_to_added_lines(self) -> ReviewCase:
        """Reject mislabelled cases at load time instead of silently scoring them.

        An expected finding on a context or removed line can never be produced by a
        correct system (findings must anchor to added lines), so such a label would
        depress recall for the wrong reason.
        """
        parsed = parse_diff(self.diff_text)
        if parsed.is_empty:
            raise ValueError(f"case {self.id!r} has no parseable diff")
        added: dict[str, set[int]] = {file.display_path: file.added_line_numbers for file in parsed.files}
        for label in self.expected:
            lines = added.get(label.path)
            if lines is None:
                raise ValueError(f"case {self.id!r} labels a path absent from its diff: {label.path}")
            if label.line not in lines:
                raise ValueError(
                    f"case {self.id!r} expects a finding at {label.path}:{label.line}, "
                    "which the diff did not add"
                )
        return self


class RetrievalDataset(_Base):
    name: str
    version: str = "1"
    kind: Literal["retrieval"]
    repository: str
    commit_sha: str
    description: str | None = None
    provenance: str | None = None
    license_note: str | None = None
    limitations: str | None = None
    cases: list[RetrievalCase]

    @field_validator("cases")
    @classmethod
    def _unique_case_ids(cls, value: list[RetrievalCase]) -> list[RetrievalCase]:
        ids = [case.id for case in value]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate case id in dataset")
        return value


class ReviewDataset(_Base):
    name: str
    version: str = "1"
    kind: Literal["review"]
    repository: str
    commit_sha: str | None = None
    description: str | None = None
    provenance: str | None = None
    license_note: str | None = None
    limitations: str | None = None
    cases: list[ReviewCase]

    @field_validator("cases")
    @classmethod
    def _unique_case_ids(cls, value: list[ReviewCase]) -> list[ReviewCase]:
        ids = [case.id for case in value]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate case id in dataset")
        return value


class TestGenerationMutation(_Base):
    """A behaviour-changing edit used to measure regression detection.

    A useful generated test passes on the original source and fails on the mutation.
    The runner never treats a test that fails on both as a detection.
    """

    id: str
    mutated_source: str
    note: str | None = None


class TestGenerationCase(_Base):
    """A self-contained target symbol and optional mutations.

    Source is embedded so the case does not depend on a mutable checkout. Validation
    checks the target path, parses the source with the standard library AST, and
    verifies that the named symbol exists. This is dataset validation, not execution.
    """

    id: str
    target_path: str
    target_symbol: str
    source: str
    signature: str | None = None
    related_tests: list[str] = Field(default_factory=list)
    mutations: list[TestGenerationMutation] = Field(default_factory=list)
    split: Literal["train", "dev", "test"] = "test"
    notes: str | None = None

    @field_validator("target_path")
    @classmethod
    def _safe_target_path(cls, value: str) -> str:
        if not value.endswith(".py") or not is_safe_relative_path(value):
            raise ValueError("target_path must be a safe repository-relative Python file")
        return value

    @model_validator(mode="after")
    def _targets_exist_and_mutations_are_unique(self) -> TestGenerationCase:
        tree = _validate_target_source(self.source, None)
        _require_symbol(tree, self.target_symbol, case_id=self.id)
        ids = [mutation.id for mutation in self.mutations]
        if len(set(ids)) != len(ids):
            raise ValueError(f"case {self.id!r} has duplicate mutation ids")
        for mutation in self.mutations:
            mutated_tree = _validate_target_source(mutation.mutated_source, None)
            _require_symbol(mutated_tree, self.target_symbol, case_id=self.id)
            if mutation.mutated_source == self.source:
                raise ValueError(f"case {self.id!r} mutation {mutation.id!r} does not change the source")
        return self


class TestGenerationDataset(_Base):
    name: str
    version: str = "1"
    kind: Literal["test_generation"]
    repository: str
    commit_sha: str | None = None
    description: str | None = None
    provenance: str | None = None
    license_note: str | None = None
    limitations: str | None = None
    cases: list[TestGenerationCase]

    @field_validator("cases")
    @classmethod
    def _unique_case_ids(cls, value: list[TestGenerationCase]) -> list[TestGenerationCase]:
        ids = [case.id for case in value]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate case id in dataset")
        return value


Dataset = RetrievalDataset | ReviewDataset | TestGenerationDataset


def _validate_target_source(source: str, path: str | None) -> ast.Module:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        label = f" in {path}" if path else ""
        raise ValueError(f"target source{label} is not parseable Python: {exc}") from exc
    return tree


def _require_symbol(tree: ast.Module, qualified_name: str, *, case_id: str) -> None:
    """Resolve a (possibly dotted) function/class name in the AST.

    This catches a dataset whose source and target label disagree before a run spends
    provider calls on a target that cannot exist.
    """
    parts = qualified_name.split(".")
    nodes: list[ast.AST] = list(tree.body)
    for part in parts:
        match = next(
            (
                node
                for node in nodes
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == part
            ),
            None,
        )
        if match is None:
            raise ValueError(f"case {case_id!r} target {qualified_name!r} is not defined in its source")
        nodes = list(getattr(match, "body", []))


def load_dataset(path: Path) -> Dataset:
    """Parse and validate one dataset file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DatasetError(f"cannot read dataset {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DatasetError(f"dataset {path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise DatasetError(f"dataset {path} must be a JSON object")
    kind = raw.get("kind")
    model: type[RetrievalDataset] | type[ReviewDataset] | type[TestGenerationDataset]
    if kind == "retrieval":
        model = RetrievalDataset
    elif kind == "review":
        model = ReviewDataset
    elif kind == "test_generation":
        model = TestGenerationDataset
    else:
        raise DatasetError(
            f"dataset {path} has unsupported kind {kind!r} (expected retrieval/review/test_generation)"
        )
    try:
        return model.model_validate(raw)
    except ValueError as exc:
        raise DatasetError(f"dataset {path} failed validation: {exc}") from exc


def content_hash(dataset: Dataset) -> str:
    canonical = json.dumps(
        dataset.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def dataset_limitations(dataset: Dataset) -> list[str]:
    """Limitations that must travel with every run over this dataset."""
    limits: list[str] = []
    if dataset.limitations:
        limits.append(dataset.limitations)
    cases = dataset.cases
    if len(cases) < 10:
        limits.append(
            f"small dataset: {len(cases)} case(s); confidence intervals are not reported and "
            "a single case can move the aggregate substantially"
        )
    splits = {case.split for case in cases}
    if splits == {"test"}:
        limits.append(
            "all cases are in the test split and there is no dev/train split: the dataset was "
            "labelled by the author, not collected independently"
        )
    return limits


def seed_dataset(session: Session, dataset: Dataset, *, source_path: Path | None = None) -> EvalDataset:
    """Insert the dataset, or return the existing row when it is identical.

    Mismatching content under an existing ``(name, version)`` is an error: silently
    replacing labels would invalidate every previously recorded run.
    """
    digest = content_hash(dataset)
    existing = session.scalar(
        select(EvalDataset).where(EvalDataset.name == dataset.name, EvalDataset.version == dataset.version)
    )
    if existing is not None:
        if existing.content_hash != digest:
            raise DatasetError(
                f"dataset {dataset.name!r} version {dataset.version!r} already exists with a "
                "different content hash; bump the version to change the labels"
            )
        return existing

    split_counts: dict[str, int] = {}
    for case in dataset.cases:
        split_counts[case.split] = split_counts.get(case.split, 0) + 1
    row = EvalDataset(
        name=dataset.name,
        version=dataset.version,
        kind=dataset.kind,
        repository=dataset.repository,
        description=dataset.description,
        commit_sha=dataset.commit_sha,
        provenance=dataset.provenance,
        license_note=dataset.license_note,
        limitations="\n".join(dataset_limitations(dataset)) or dataset.limitations,
        source_path=str(source_path) if source_path is not None else None,
        case_count=len(dataset.cases),
        split_counts=split_counts,
        content_hash=digest,
    )
    session.add(row)
    session.flush()
    for case in dataset.cases:
        if isinstance(case, RetrievalCase):
            labels: list[dict[str, Any]] = [
                {
                    "path": label.path,
                    "qualified_name": label.qualified_name,
                    "grade": label.grade,
                    "note": label.note,
                }
                for label in case.relevant
            ]
            payload: dict[str, Any] = {"kind": "retrieval"}
            query = case.query
        elif isinstance(case, TestGenerationCase):
            labels = []
            payload = {
                "kind": "test_generation",
                "target_path": case.target_path,
                "target_symbol": case.target_symbol,
                "source": case.source,
                "signature": case.signature,
                "related_tests": case.related_tests,
                "mutations": [mutation.model_dump(mode="json") for mutation in case.mutations],
            }
            query = case.target_symbol
        else:
            labels = []
            payload = {
                "kind": "review",
                "diff_text": case.diff_text,
                "commit_sha": case.commit_sha,
                "expected": [label.model_dump(mode="json") for label in case.expected],
            }
            query = case.id
        session.add(
            EvalCase(
                dataset_id=row.id,
                case_key=case.id,
                split=case.split,
                query=query,
                payload=payload,
                labels=labels,
                notes=case.notes,
                provenance=dataset.provenance,
            )
        )
    # Sessions run with autoflush disabled; callers query the cases immediately
    # after seeding, so make them visible here instead of relying on the caller.
    session.flush()
    return row


def load_cases(session: Session, dataset: EvalDataset) -> list[EvalCase]:
    return list(
        session.scalars(select(EvalCase).where(EvalCase.dataset_id == dataset.id).order_by(EvalCase.case_key))
    )


__all__ = [
    "Dataset",
    "DatasetError",
    "EvalDataset",
    "ExpectedFinding",
    "RetrievalCase",
    "RetrievalDataset",
    "RetrievalLabel",
    "ReviewCase",
    "ReviewDataset",
    "content_hash",
    "dataset_limitations",
    "load_cases",
    "load_dataset",
    "seed_dataset",
]
