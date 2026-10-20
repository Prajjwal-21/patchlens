"""Map a diff onto the symbols it changes.

The diff says which *lines* changed; review checks and prompts need *symbols*.
Two sources are combined, in order of trust:

1. the **indexed snapshot** (commit the repository was last indexed at) - it gives
   stable ``symbol_id`` values that link a finding back to the code graph;
2. the **new-side file content**, when PatchLens can read it (local checkout or an
   explicit file map) - it is re-parsed so symbol boundaries match the post-change
   code rather than the snapshot.

When neither is available the diff is still reviewed: added lines are grouped into
a synthetic module-level symbol so line checks and the model still run. The
limitation is recorded in ``notes`` instead of being hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from patchlens.core.config import Settings, get_settings
from patchlens.core.logging import describe_exception, get_logger, log_extra
from patchlens.core.security import is_safe_relative_path, resolve_within
from patchlens.db.models.repository import CodeSymbol, Repository
from patchlens.services.ingestion.discovery import is_test_path
from patchlens.services.ingestion.python_parser import FileParseResult, PythonParser
from patchlens.services.ingestion.test_mapping import TestMatch, TestTarget, match_tests_for_symbol
from patchlens.services.retrieval.service import resolve_version
from patchlens.services.review.diff import ParsedDiff, apply_hunks_to_text, new_side_lines, new_side_matches
from patchlens.services.review.types import ChangedSymbol

logger = get_logger(__name__)

#: Never read more than this much of a changed file when re-parsing it.
MAX_FILE_READ_BYTES = 1_500_000
#: Cap on the source attached to a changed symbol (prompt/DB size guard).
MAX_SYMBOL_SOURCE_CHARS = 6_000
#: How many related tests to keep per symbol.
MAX_TEST_MATCHES = 5


@dataclass(slots=True)
class SymbolMapping:
    """Result of mapping a diff onto symbols."""

    changed_symbols: list[ChangedSymbol] = field(default_factory=list)
    #: New-side text of changed files that could be read, keyed by diff path.
    file_texts: dict[str, str] = field(default_factory=dict)
    #: ``qualified_name -> ranked related tests``.
    test_matches: dict[str, list[TestMatch]] = field(default_factory=dict)
    version_id: str | None = None
    indexed_paths: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    reparse_failures: dict[str, str] = field(default_factory=dict)

    @property
    def changed_paths(self) -> list[str]:
        return sorted({symbol.path for symbol in self.changed_symbols})

    @property
    def test_file_count(self) -> int:
        return sum(1 for symbol in self.changed_symbols if symbol.is_test)

    def to_dict(self) -> dict[str, object]:
        return {
            "version_id": self.version_id,
            "changed_symbols": [symbol.to_payload() for symbol in self.changed_symbols],
            "files_read": sorted(self.file_texts),
            "indexed_files": len(self.indexed_paths),
            "related_test_queries": {
                name: [match.to_dict() for match in matches] for name, matches in self.test_matches.items()
            },
            "notes": self.notes,
            "reparse_failures": self.reparse_failures,
        }


def repository_root(repository: Repository) -> Path | None:
    """Local checkout root for a repository, if it has one that exists."""
    if not repository.local_path:
        return None
    path = Path(repository.local_path).expanduser()
    try:
        if not path.is_dir():
            return None
    except OSError:  # pragma: no cover - unreadable mount
        return None
    return path


def read_changed_files(
    *,
    repository: Repository,
    diff: ParsedDiff,
    max_file_bytes: int = MAX_FILE_READ_BYTES,
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Read the **new side** of changed Python files from a local checkout.

    The new side is what the pull request proposes, and it is reconstructed from
    the diff when the checkout is still at the base revision. Without that step an
    AST check would happily analyse the pre-change code and report on lines that
    no longer exist.

    Returns ``(texts, failures, notes)``. Paths are normalised and contained
    inside the checkout root before any read, so a diff cannot escape it.
    """
    texts: dict[str, str] = {}
    failures: dict[str, str] = {}
    notes: list[str] = []
    reconstructed: list[str] = []
    root = repository_root(repository)
    if root is None:
        notes.append(
            "no local checkout is configured for this repository: deterministic AST checks and "
            "post-change symbol boundaries were unavailable"
        )
        return texts, failures, notes

    for file in diff.files:
        if file.status == "deleted" or file.is_binary:
            continue
        path = file.display_path
        if not path.endswith(".py"):
            continue
        if not is_safe_relative_path(path):
            # A diff is attacker-controlled input. Say that the path was rejected
            # instead of dropping it silently: an invisible skip is a review gap.
            failures[path] = "path rejected: not a contained relative path"
            continue
        try:
            absolute = resolve_within(root, path)
        except ValueError as exc:
            failures[path] = f"path rejected: {exc}"
            continue
        try:
            if not absolute.is_file():
                if file.status == "added":
                    # Every line of an added file is in the diff, so the new side
                    # is reconstructible without touching the filesystem.
                    texts[path] = "\n".join(new_side_lines(file)) + "\n"
                    reconstructed.append(path)
                    continue
                failures[path] = "file not present in the local checkout"
                continue
            size = absolute.stat().st_size
            if size > max_file_bytes:
                failures[path] = f"file is {size} bytes, above the {max_file_bytes}-byte review read limit"
                continue
            on_disk = absolute.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            failures[path] = f"read failed: {exc}"
            continue

        if not file.hunks:
            texts[path] = on_disk
            continue
        if new_side_matches(file, on_disk):
            texts[path] = on_disk
            continue
        patched = apply_hunks_to_text(file, on_disk)
        if patched is not None and new_side_matches(file, patched):
            texts[path] = patched
            reconstructed.append(path)
            continue
        # Neither revision matches the diff: review from the diff itself rather
        # than pretending the on-disk content is the pull request's version.
        failures[path] = "checkout content matches neither side of this diff; AST checks used the diff only"

    if reconstructed:
        notes.append(
            f"{len(reconstructed)} changed file(s) were reconstructed from the diff because the "
            "checkout is not at the pull request head: " + ", ".join(sorted(reconstructed)[:5])
        )
    if failures:
        notes.append(
            f"{len(failures)} changed file(s) could not be read for full-content checks; "
            "they were still reviewed from the diff itself"
        )
    return texts, failures, notes


def load_indexed_symbols(
    session: Session, repository: Repository
) -> tuple[str | None, dict[str, list[CodeSymbol]], list[CodeSymbol]]:
    """Return the newest indexed version's symbols grouped by path, plus test symbols."""
    version = resolve_version(session, repository)
    if version is None:
        return None, {}, []
    rows = session.scalars(
        select(CodeSymbol)
        .where(CodeSymbol.repository_version_id == version.id)
        .order_by(CodeSymbol.path, CodeSymbol.start_line)
    ).all()
    by_path: dict[str, list[CodeSymbol]] = {}
    for row in rows:
        by_path.setdefault(row.path, []).append(row)
    return version.id, by_path, [row for row in rows if row.is_test]


def build_test_targets(test_symbols: list[CodeSymbol], *, names_only: bool = False) -> list[TestTarget]:
    """Convert indexed test symbols into the matcher's target type."""
    targets: list[TestTarget] = []
    for symbol in test_symbols:
        if not names_only and symbol.kind == "module":
            continue
        targets.append(
            TestTarget(
                symbol_id=symbol.id,
                path=symbol.path,
                qualified_name=symbol.qualified_name,
                name=symbol.name,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                calls=tuple(symbol.calls or ()),
                imports=tuple(symbol.imports or ()),
            )
        )
    return targets


def diff_test_targets(file_texts: dict[str, str], parser: PythonParser) -> list[TestTarget]:
    """Tests added or changed by this diff, parsed from the new side.

    The index only knows the corpus at the base commit. Without this, a pull request
    that adds a method *and* its test looks untested, which is a false positive the
    author cannot act on. These targets are not persisted; they exist for this review
    only and are identified with a ``diff:`` prefix.
    """
    targets: list[TestTarget] = []
    for path, text in sorted(file_texts.items()):
        if not path.endswith(".py") or not is_test_path(path):
            continue
        result = parser.parse(path=path, text=text, is_test_file=True)
        if not result.ok:
            continue
        for symbol in result.symbols:
            # File-level imports live on the module symbol. Treating it as a test
            # target would mark every symbol in the imported module as covered.
            if not symbol.is_test or symbol.kind == "module":
                continue
            targets.append(
                TestTarget(
                    symbol_id=f"diff:{path}:{symbol.qualified_name}",
                    path=path,
                    qualified_name=symbol.qualified_name,
                    name=symbol.name,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    calls=tuple(symbol.calls),
                    imports=tuple(symbol.imports),
                )
            )
    return targets


def merge_test_targets(indexed: list[TestTarget], changed: list[TestTarget]) -> list[TestTarget]:
    """Merge targets, preferring the new side for tests changed in this diff."""
    merged: dict[tuple[str, str], TestTarget] = {
        (target.path, target.qualified_name): target for target in indexed
    }
    for target in changed:
        merged[(target.path, target.qualified_name)] = target
    return list(merged.values())


def match_related_tests(
    changed_symbols: list[ChangedSymbol], test_targets: list[TestTarget], *, limit: int = MAX_TEST_MATCHES
) -> dict[str, list[TestMatch]]:
    matches: dict[str, list[TestMatch]] = {}
    for symbol in changed_symbols:
        if symbol.is_test:
            continue
        found = match_tests_for_symbol(
            source_path=symbol.path,
            source_qualified_name=symbol.qualified_name,
            source_name=symbol.qualified_name.split(".")[-1],
            candidates=test_targets,
            limit=limit,
        )
        if found:
            matches[symbol.qualified_name] = found
    return matches


def map_changed_symbols(
    session: Session,
    *,
    repository: Repository,
    diff: ParsedDiff,
    settings: Settings | None = None,
    file_texts: dict[str, str] | None = None,
) -> SymbolMapping:
    """Build the review's symbol view of ``diff``."""
    settings = settings or get_settings()
    mapping = SymbolMapping()

    version_id, indexed_by_path, test_symbols = load_indexed_symbols(session, repository)
    mapping.version_id = version_id
    mapping.indexed_paths = set(indexed_by_path)

    if file_texts is None:
        file_texts, failures, notes = read_changed_files(
            repository=repository, diff=diff, max_file_bytes=settings.index_max_file_bytes
        )
        mapping.reparse_failures = failures
        mapping.notes.extend(notes)
    mapping.file_texts = file_texts

    parser = PythonParser()
    for file in diff.files:
        if file.status == "deleted" or file.is_binary:
            continue
        path = file.display_path
        if not path.endswith(".py"):
            continue
        added = diff.added_lines_for(path)
        if not added:
            continue
        is_test = is_test_path(path)
        text = file_texts.get(path)
        if text is not None:
            result = _parse_current(path, text, is_test=is_test, parser=parser)
            if result is not None and result.ok:
                _symbols_from_parse(
                    target=mapping.changed_symbols,
                    parsed=result,
                    indexed=indexed_by_path.get(path, []),
                    added=added,
                    is_test=is_test,
                )
                continue
            reason = result.error if result is not None else "unparsable new-side content"
            mapping.reparse_failures.setdefault(path, reason or "unknown parse failure")
            mapping.notes.append(
                f"{path} could not be parsed after the change ({reason}); symbol boundaries come from the "
                "indexed snapshot and may lag the diff"
            )
        _symbols_from_index(
            target=mapping.changed_symbols,
            indexed=indexed_by_path.get(path, []),
            added=added,
            path=path,
            is_test=is_test,
        )

    if not mapping.changed_symbols:
        mapping.notes.append("the diff contains no added Python lines to review")

    changed_tests = diff_test_targets(mapping.file_texts, parser)
    if changed_tests:
        mapping.notes.append(
            f"{len(changed_tests)} test(s) added or changed in this diff were considered when "
            "associating changed code with tests"
        )
    targets = merge_test_targets(build_test_targets(test_symbols), changed_tests)
    mapping.test_matches = match_related_tests(mapping.changed_symbols, targets)
    logger.debug(
        "symbol mapping complete",
        extra=log_extra(
            changed_symbols=len(mapping.changed_symbols),
            files_read=len(mapping.file_texts),
            tests=len(targets),
            reparse_failures=len(mapping.reparse_failures),
        ),
    )
    return mapping


def build_queries(symbols: list[ChangedSymbol], diff: ParsedDiff, *, max_chars: int = 700) -> list[str]:
    """Retrieval queries: one per changed symbol, built from the change itself."""
    queries: list[str] = []
    for symbol in symbols:
        parts = [symbol.qualified_name, symbol.path]
        if symbol.signature:
            parts.append(symbol.signature)
        if symbol.docstring:
            parts.append(symbol.docstring.splitlines()[0])
        added_text = [
            text
            for line in symbol.changed_lines[:20]
            if (text := diff.added_line_text(symbol.path, line)) is not None
        ]
        parts.extend(added_text)
        query = " ".join(part.strip() for part in parts if part and part.strip())
        if query:
            queries.append(query[:max_chars])
    # Deduplicate while preserving order; keep a bounded number of queries.
    seen: set[str] = set()
    unique: list[str] = []
    for query in queries:
        if query in seen:
            continue
        seen.add(query)
        unique.append(query)
    return unique[:20]


def _parse_current(path: str, text: str, *, is_test: bool, parser: PythonParser) -> FileParseResult | None:
    try:
        return parser.parse(path=path, text=text, is_test_file=is_test)
    except Exception as exc:  # a crash in the parser must not lose the review
        logger.warning(
            "re-parsing changed file failed", extra=log_extra(path=path, **describe_exception(exc))
        )
        return None


def _symbols_from_parse(
    *,
    target: list[ChangedSymbol],
    parsed: FileParseResult,
    indexed: list[CodeSymbol],
    added: set[int],
    is_test: bool,
) -> None:
    indexed_by_qualified = {symbol.qualified_name: symbol for symbol in indexed}
    covered: set[int] = set()
    for symbol in parsed.symbols:
        if symbol.kind == "module":
            continue
        changed = sorted(line for line in added if symbol.start_line <= line <= symbol.end_line)
        if not changed:
            continue
        covered.update(changed)
        previous = indexed_by_qualified.get(symbol.qualified_name)
        source, truncated = _truncate(symbol.source)
        target.append(
            ChangedSymbol(
                symbol_id=previous.id if previous is not None else None,
                path=symbol.path,
                qualified_name=symbol.qualified_name,
                kind=symbol.kind.value if hasattr(symbol.kind, "value") else str(symbol.kind),
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                source=source,
                signature=symbol.signature,
                docstring=symbol.docstring,
                is_test=symbol.is_test or is_test,
                source_truncated=truncated,
                changed_lines=changed,
                detection="parsed_current",
            )
        )
    leftovers = sorted(added - covered)
    if leftovers:
        target.append(
            _module_symbol(parsed.path, leftovers, is_test=is_test, source=parsed.module_docstring or "")
        )


def _symbols_from_index(
    *, target: list[ChangedSymbol], indexed: list[CodeSymbol], added: set[int], path: str, is_test: bool
) -> None:
    covered: set[int] = set()
    for symbol in indexed:
        if symbol.kind == "module":
            continue
        changed = sorted(line for line in added if symbol.start_line <= line <= symbol.end_line)
        if not changed:
            continue
        covered.update(changed)
        source, truncated = _truncate(symbol.source or "")
        target.append(
            ChangedSymbol(
                symbol_id=symbol.id,
                path=symbol.path,
                qualified_name=symbol.qualified_name,
                kind=symbol.kind,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                source=source,
                signature=symbol.signature,
                docstring=symbol.docstring,
                is_test=bool(symbol.is_test or is_test),
                source_truncated=bool(symbol.source_truncated or truncated),
                changed_lines=changed,
                detection="indexed_snapshot",
            )
        )
    leftovers = sorted(added - covered)
    if leftovers:
        target.append(_module_symbol(path, leftovers, is_test=is_test, source=""))


def _module_symbol(path: str, added: list[int], *, is_test: bool, source: str) -> ChangedSymbol:
    """Fallback symbol for added lines no symbol covers (new module, top-level code)."""
    return ChangedSymbol(
        symbol_id=None,
        path=path,
        qualified_name=f"{Path(path).stem}:module",
        kind="module",
        start_line=added[0],
        end_line=added[-1],
        source=source,
        is_test=is_test,
        changed_lines=added,
        detection="added_lines_fallback",
    )


def _truncate(source: str, limit: int = MAX_SYMBOL_SOURCE_CHARS) -> tuple[str, bool]:
    if len(source) <= limit:
        return source, False
    return source[:limit], True


__all__ = [
    "MAX_SYMBOL_SOURCE_CHARS",
    "SymbolMapping",
    "build_queries",
    "build_test_targets",
    "diff_test_targets",
    "load_indexed_symbols",
    "map_changed_symbols",
    "match_related_tests",
    "merge_test_targets",
    "read_changed_files",
    "repository_root",
]
