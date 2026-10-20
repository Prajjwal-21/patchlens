"""Test-to-source association.

Given a changed source symbol, work out which existing tests most plausibly
exercise it, so the review can (a) recommend tests to run and (b) flag changed
behaviour that no test appears to cover.

All signals are structural (paths, imports, call names). Nothing here claims to
be dynamic coverage; the confidence of each association is reported as a reason
string plus a heuristic score.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from patchlens.services.ingestion.python_parser import module_name_for_path

#: Weights are documented heuristics, not learned parameters.
WEIGHT_FILENAME_MATCH = 1.0
WEIGHT_DIRECT_CALL = 0.85
WEIGHT_MODULE_IMPORT = 0.6
WEIGHT_PACKAGE_SIBLING = 0.35
WEIGHT_NAME_TOKEN_OVERLAP = 0.3
WEIGHT_SHARED_SUBJECT = 0.45


@dataclass(frozen=True, slots=True)
class TestTarget:
    """A test discovered in the index."""

    symbol_id: str
    path: str
    qualified_name: str
    name: str
    start_line: int
    end_line: int
    calls: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()

    @property
    def is_test(self) -> bool:
        return self.name.startswith("test") or "test" in Path(self.path).parts


@dataclass(frozen=True, slots=True)
class TestMatch:
    target: TestTarget
    score: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "test_path": self.target.path,
            "test_name": self.target.qualified_name,
            "start_line": self.target.start_line,
            "end_line": self.target.end_line,
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
            "reason": "; ".join(self.reasons),
        }


def _basename_variants(module_name: str) -> set[str]:
    leaf = module_name.split(".")[-1]
    return {f"test_{leaf}.py", f"{leaf}_test.py"}


def _tokenise(value: str) -> set[str]:
    tokens: set[str] = set()
    for part in value.replace(".", "_").split("_"):
        if len(part) > 2 and part not in {"test", "tests", "the", "and"}:
            tokens.add(part.lower())
    return tokens


def match_tests_for_symbol(
    *,
    source_path: str,
    source_qualified_name: str,
    source_name: str,
    candidates: list[TestTarget],
    limit: int = 5,
) -> list[TestMatch]:
    """Rank tests that plausibly cover ``source_qualified_name``.

    Signals, in decreasing strength:

    1. file name mirrors the source module (``pkg/mod.py`` -> ``test_mod.py``);
    2. the test calls the changed symbol by name;
    3. the test imports the changed module;
    4. same package / directory;
    5. shared identifier tokens and shared subject under test (``SUT`` import).
    """
    module = module_name_for_path(source_path)
    module_leaf = module.split(".")[-1]
    variants = _basename_variants(module)
    source_tokens = _tokenise(source_qualified_name)
    source_dir = str(Path(source_path).parent)

    matches: list[TestMatch] = []
    for target in candidates:
        score = 0.0
        reasons: list[str] = []
        target_name = Path(target.path).name

        if target_name in variants:
            score += WEIGHT_FILENAME_MATCH
            reasons.append(f"test file name mirrors module {module_leaf!r}")

        called = {call.split(".")[-1] for call in target.calls}
        if source_name in called or source_qualified_name in target.calls:
            score += WEIGHT_DIRECT_CALL
            reasons.append(f"test calls {source_name}(...)")

        imported = {item.lstrip(".") for item in target.imports}
        if module in imported or any(item.endswith(module) for item in imported):
            score += WEIGHT_MODULE_IMPORT
            reasons.append(f"test imports {module}")
        elif module_leaf in {item.split(".")[-1] for item in imported}:
            score += WEIGHT_MODULE_IMPORT / 2
            reasons.append(f"test imports module named {module_leaf!r}")

        if str(Path(target.path).parent) == source_dir:
            score += WEIGHT_PACKAGE_SIBLING
            reasons.append("test lives in the same directory")

        overlap = source_tokens & _tokenise(target.qualified_name)
        if overlap:
            score += WEIGHT_NAME_TOKEN_OVERLAP
            reasons.append(f"shared identifiers: {', '.join(sorted(overlap))}")

        if score > 0.0:
            matches.append(TestMatch(target=target, score=score, reasons=tuple(reasons)))

    matches.sort(key=lambda item: (-item.score, item.target.path, item.target.start_line))
    return matches[:limit]


def has_plausible_coverage(matches: list[TestMatch], *, threshold: float = 0.6) -> bool:
    """True when at least one association is strong enough to count as coverage.

    Score alone is not enough. A test file whose *name* mirrors the module scores
    ``1.0`` even when it never mentions the changed symbol, so a mirrored file
    only counts as coverage when the test also imports the module or shares
    identifiers with the symbol. Weak signals (sibling directory, token overlap)
    never suppress a "no test covers this" finding.
    """
    for match in matches:
        if match.score < threshold:
            continue
        joined = " ".join(match.reasons)
        if "calls " in joined or "imports " in joined:
            return True
        if "mirrors module" in joined and "shared identifiers" in joined:
            return True
    return False


__all__ = [
    "TestMatch",
    "TestTarget",
    "has_plausible_coverage",
    "match_tests_for_symbol",
]
