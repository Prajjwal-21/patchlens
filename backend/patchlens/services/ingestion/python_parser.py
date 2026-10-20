"""Python source parsing built on :mod:`ast`.

Extracts modules, classes, functions, methods and tests with:

* real source line ranges (used to validate LLM citations),
* reconstructed signatures,
* docstrings,
* decorators and base classes,
* approximate call/import edges.

Parsing is deliberately tolerant: a file containing syntax errors yields a
:class:`FileParseResult` with ``error`` set and is reported, never silently
dropped.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from patchlens.core.enums import SymbolKind
from patchlens.core.security import content_fingerprint

MAX_DOCSTRING_CHARS = 2_000
MAX_EMBED_DOC_CHARS = 6_000


@dataclass(slots=True)
class ParsedSymbol:
    """A single extractable symbol."""

    kind: SymbolKind
    name: str
    qualified_name: str
    path: str
    start_line: int
    end_line: int
    signature: str | None = None
    docstring: str | None = None
    parent_qualified_name: str | None = None
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    source: str = ""
    source_truncated: bool = False
    is_test: bool = False

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.source) // 4)

    @property
    def source_hash(self) -> str:
        return content_fingerprint(self.path, self.qualified_name, self.source, length=64)


@dataclass(slots=True)
class ParseEdge:
    kind: str
    src_qualified_name: str | None
    dst_name: str
    path: str
    line: int | None = None


@dataclass(slots=True)
class FileParseResult:
    path: str
    module_name: str
    is_test_file: bool
    symbols: list[ParsedSymbol] = field(default_factory=list)
    edges: list[ParseEdge] = field(default_factory=list)
    module_imports: list[str] = field(default_factory=list)
    module_docstring: str | None = None
    error: str | None = None
    error_line: int | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def module_name_for_path(path: str) -> str:
    """``src/pkg/mod.py`` -> ``pkg.mod``; ``pkg/__init__.py`` -> ``pkg``."""
    parts = list(Path(path).with_suffix("").parts)
    if not parts:
        return Path(path).stem
    # Drop a leading conventional source root so imports match reality.
    if len(parts) > 1 and parts[0] in {"src", "lib", "app", "source"}:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return Path(path).stem
    return ".".join(part for part in parts if part.isidentifier())


def _format_annotation(node: ast.expr | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is very tolerant
        return "?"


def _format_default(node: ast.expr) -> str:
    try:
        rendered = ast.unparse(node)
    except Exception:  # pragma: no cover
        return "..."
    return rendered.replace("=", "=")[:60]


def format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_method: bool) -> str:
    """Reconstruct a readable ``def`` signature including annotations/defaults."""
    args = node.args
    positional: list[str] = []
    all_positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(all_positional) - len(args.defaults)) + list(
        args.defaults
    )

    posonly_count = len(args.posonlyargs)
    for index, (arg, default) in enumerate(zip(all_positional, defaults, strict=True)):
        if is_method and index == 0 and arg.arg in {"self", "cls"}:
            continue
        rendering = arg.arg
        if arg.annotation is not None:
            rendering += f": {_format_annotation(arg.annotation)}"
        if default is not None:
            rendering += f" = {_format_default(default)}"
        positional.append(rendering)
        if posonly_count and index == posonly_count - 1:
            positional.append("/")

    if args.vararg is not None:
        vararg = f"*{args.vararg.arg}"
        if args.vararg.annotation is not None:
            vararg += f": {_format_annotation(args.vararg.annotation)}"
        positional.append(vararg)
    elif args.kwonlyargs:
        positional.append("*")

    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        rendering = arg.arg
        if arg.annotation is not None:
            rendering += f": {_format_annotation(arg.annotation)}"
        if default is not None:
            rendering += f" = {_format_default(default)}"
        positional.append(rendering)

    if args.kwarg is not None:
        kwarg = f"**{args.kwarg.arg}"
        if args.kwarg.annotation is not None:
            kwarg += f": {_format_annotation(args.kwarg.annotation)}"
        positional.append(kwarg)

    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {_format_annotation(node.returns)}" if node.returns is not None else ""
    return f"{prefix} {node.name}({', '.join(positional)}){returns}"


def _decorator_name(node: ast.expr) -> str:
    try:
        rendered = ast.unparse(node)
    except Exception:  # pragma: no cover
        return "?"
    return rendered.split("(")[0].strip()[:120]


def _call_names(body: list[ast.stmt]) -> list[str]:
    """Approximate callee names reached from a symbol body (deduplicated)."""
    found: list[str] = []
    seen: set[str] = set()
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call):
                target = node.func
                name: str | None = None
                if isinstance(target, ast.Name):
                    name = target.id
                elif isinstance(target, ast.Attribute):
                    name = target.attr
                    if isinstance(target.value, ast.Name):
                        name = f"{target.value.id}.{target.attr}"
                if name and name not in seen:
                    seen.add(name)
                    found.append(name)
                if len(found) >= 80:
                    return found
    return found


def _import_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = "." * node.level + base
            names.append(base)
    return sorted(set(filter(None, names)))


def _is_test_function(name: str, decorators: list[str], in_test_file: bool) -> bool:
    if name.startswith("test_") or name.endswith("_test"):
        return True
    if any("mark.parametrize" in decorator or decorator.endswith("fixture") for decorator in decorators):
        return in_test_file
    return in_test_file and name.startswith("test")


def _is_test_class(bases: list[str], methods: list[ast.FunctionDef | ast.AsyncFunctionDef]) -> bool:
    if any(
        base.split(".")[-1] in {"TestCase", "IsolatedAsyncioTestCase", "unittest.TestCase"} for base in bases
    ):
        return True
    return any(method.name.startswith("test") for method in methods)


class PythonParser:
    """Stateless AST parser; safe to share across threads."""

    max_symbols_per_file = 2_000

    def parse(self, *, path: str, text: str, is_test_file: bool = False) -> FileParseResult:
        module_name = module_name_for_path(path)
        result = FileParseResult(path=path, module_name=module_name, is_test_file=is_test_file)
        lines = text.splitlines()

        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            result.error = f"SyntaxError: {exc.msg}"
            result.error_line = exc.lineno
            return result
        except (ValueError, RecursionError) as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            return result

        result.module_imports = _import_names(tree)
        result.module_docstring = (ast.get_docstring(tree, clean=True) or "")[:MAX_DOCSTRING_CHARS] or None

        module_symbol = ParsedSymbol(
            kind=SymbolKind.MODULE,
            name=module_name,
            qualified_name=module_name,
            path=path,
            start_line=1,
            end_line=max(1, len(lines)),
            docstring=result.module_docstring,
            imports=result.module_imports,
            source=self._slice(lines, 1, max(1, len(lines)))[0],
            is_test=is_test_file,
        )
        result.symbols.append(module_symbol)
        result.edges.append(
            ParseEdge(kind="import", src_qualified_name=module_name, dst_name=module_name, path=path, line=1)
        )

        self._collect(
            tree.body, result, lines, prefix="", parent=None, is_test_file=is_test_file, inside_class=False
        )
        return result

    # ----------------------------------------------------------------- internals
    def _collect(
        self,
        body: list[ast.stmt],
        result: FileParseResult,
        lines: list[str],
        *,
        prefix: str,
        parent: ParsedSymbol | None,
        is_test_file: bool,
        inside_class: bool,
    ) -> None:
        for node in body:
            if len(result.symbols) >= self.max_symbols_per_file:
                return

            if isinstance(node, ast.ClassDef):
                decorators = [_decorator_name(item) for item in node.decorator_list]
                bases = [_format_annotation(base) for base in node.bases]
                qualified = f"{prefix}{node.name}"
                start, end, source, truncated = self._symbol_source(lines, node, node.decorator_list)
                methods = [
                    item for item in node.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                ]
                is_test_class = is_test_file and _is_test_class(bases, methods)
                symbol = ParsedSymbol(
                    kind=SymbolKind.TEST_CLASS if is_test_class else SymbolKind.CLASS,
                    name=node.name,
                    qualified_name=qualified,
                    path=result.path,
                    start_line=start,
                    end_line=end,
                    docstring=(ast.get_docstring(node, clean=True) or "")[:MAX_DOCSTRING_CHARS] or None,
                    parent_qualified_name=parent.qualified_name if parent else None,
                    decorators=decorators,
                    bases=bases,
                    source=source,
                    source_truncated=truncated,
                    is_test=is_test_class,
                )
                result.symbols.append(symbol)
                for base in bases:
                    result.edges.append(
                        ParseEdge(
                            kind="inherit",
                            src_qualified_name=qualified,
                            dst_name=base.split(".")[-1],
                            path=result.path,
                            line=node.lineno,
                        )
                    )
                self._collect(
                    node.body,
                    result,
                    lines,
                    prefix=f"{qualified}.",
                    parent=symbol,
                    is_test_file=is_test_file,
                    inside_class=True,
                )
                continue

            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                decorators = [_decorator_name(item) for item in node.decorator_list]
                qualified = f"{prefix}{node.name}"
                start, end, source, truncated = self._symbol_source(lines, node, node.decorator_list)
                calls = _call_names(node.body)
                symbol_imports = sorted(_import_names(ast.Module(body=node.body, type_ignores=[])))
                is_test = inside_class and is_test_file and node.name.startswith("test")
                if not inside_class:
                    is_test = _is_test_function(node.name, decorators, is_test_file)
                symbol = ParsedSymbol(
                    kind=SymbolKind.TEST_FUNCTION
                    if is_test
                    else (SymbolKind.METHOD if inside_class else SymbolKind.FUNCTION),
                    name=node.name,
                    qualified_name=qualified,
                    path=result.path,
                    start_line=start,
                    end_line=end,
                    signature=format_signature(node, is_method=inside_class),
                    docstring=(ast.get_docstring(node, clean=True) or "")[:MAX_DOCSTRING_CHARS] or None,
                    parent_qualified_name=parent.qualified_name if parent else None,
                    decorators=decorators,
                    calls=calls,
                    imports=symbol_imports,
                    source=source,
                    source_truncated=truncated,
                    is_test=is_test,
                )
                result.symbols.append(symbol)
                for call in calls:
                    result.edges.append(
                        ParseEdge(
                            kind="call",
                            src_qualified_name=qualified,
                            dst_name=call.split(".")[-1],
                            path=result.path,
                            line=node.lineno,
                        )
                    )
                continue

    @staticmethod
    def _slice(lines: list[str], start: int, end: int) -> tuple[str, bool]:
        truncated = False
        body = "\n".join(lines[max(0, start - 1) : end])
        if len(body) > MAX_EMBED_DOC_CHARS * 4:
            body = body[: MAX_EMBED_DOC_CHARS * 4]
            truncated = True
        return body, truncated

    @classmethod
    def _symbol_source(
        cls,
        lines: list[str],
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        decorators: list[ast.expr],
    ) -> tuple[int, int, str, bool]:
        start = decorators[0].lineno if decorators else node.lineno
        start = min(start, node.lineno)
        end = getattr(node, "end_lineno", None) or node.lineno
        source, truncated = cls._slice(lines, start, end)
        return start, end, source, truncated


def build_symbol_document(symbol: ParsedSymbol) -> tuple[str, str]:
    """Return ``(document_text, summary)`` used for embedding and BM25 indexing.

    ``document_text`` is what gets embedded/full-text indexed; ``summary`` is the
    short human-readable label used in evidence lists.
    """
    docstring = (symbol.docstring or "")[:MAX_EMBED_DOC_CHARS]
    header = {
        SymbolKind.CLASS: f"class {symbol.qualified_name}",
        SymbolKind.TEST_CLASS: f"test class {symbol.qualified_name}",
        SymbolKind.FUNCTION: symbol.signature or f"def {symbol.qualified_name}",
        SymbolKind.METHOD: symbol.signature or f"def {symbol.qualified_name}",
        SymbolKind.TEST_FUNCTION: symbol.signature or f"def {symbol.qualified_name}",
        SymbolKind.MODULE: f"module {symbol.qualified_name}",
        SymbolKind.DOC: f"documentation {symbol.qualified_name}",
    }[symbol.kind]

    parts = [header, f"file: {symbol.path}"]
    if symbol.bases:
        parts.append(f"bases: {', '.join(symbol.bases)}")
    if symbol.calls:
        parts.append(f"calls: {', '.join(symbol.calls[:20])}")
    if docstring:
        parts.append(f"docstring: {docstring}")
    parts.append("source:\n" + symbol.source[:MAX_EMBED_DOC_CHARS])
    document = "\n".join(parts)

    label = f"{symbol.kind.value} {symbol.qualified_name}"
    if symbol.signature and symbol.kind != SymbolKind.MODULE:
        label += f" | {symbol.signature}"
    return document, label


__all__ = [
    "FileParseResult",
    "ParseEdge",
    "ParsedSymbol",
    "PythonParser",
    "build_symbol_document",
    "format_signature",
    "module_name_for_path",
]
