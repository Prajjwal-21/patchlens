"""Repository ingestion: discovery, AST parsing, test mapping and indexing."""

from patchlens.services.ingestion.discovery import (
    DiscoveredFile,
    DiscoveryReport,
    RepositoryWalker,
    compute_tree_hash,
    is_test_path,
)
from patchlens.services.ingestion.indexer import IndexOutcome, RepositoryIndexer, discover_files
from patchlens.services.ingestion.python_parser import (
    FileParseResult,
    ParsedSymbol,
    PythonParser,
    build_symbol_document,
    module_name_for_path,
)
from patchlens.services.ingestion.test_mapping import (
    TestMatch,
    TestTarget,
    has_plausible_coverage,
    match_tests_for_symbol,
)

__all__ = [
    "DiscoveredFile",
    "DiscoveryReport",
    "FileParseResult",
    "IndexOutcome",
    "ParsedSymbol",
    "PythonParser",
    "RepositoryIndexer",
    "RepositoryWalker",
    "TestMatch",
    "TestTarget",
    "build_symbol_document",
    "compute_tree_hash",
    "discover_files",
    "has_plausible_coverage",
    "is_test_path",
    "match_tests_for_symbol",
    "module_name_for_path",
]
