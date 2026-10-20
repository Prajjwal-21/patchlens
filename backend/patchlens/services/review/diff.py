"""Unified-diff parsing.

PatchLens must never accept a model's claim about a source location on faith, so
this module is the single source of truth for "which lines exist on the new side of
this diff". Everything downstream - finding validation, GitHub comment anchoring,
changed-symbol mapping - reads the parsed structure.

The parser is defensive by design: real diffs from GitHub contain rename headers,
mode changes, binary markers, missing trailing newlines and (occasionally)
malformed hunk headers. Anything unrecognised becomes a recorded warning rather
than a crash or a silent truncation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

LineKind = Literal["context", "add", "remove", "meta"]
FileStatus = Literal["added", "deleted", "modified", "renamed", "binary"]

_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<heading>.*)$"
)
_DIFF_HEADER_RE = re.compile(r"^diff --git (?P<old>\S+) (?P<new>\S+)$")
_INDEX_RE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+")
_MODE_RE = re.compile(r"^(old|new|deleted file|new file) mode \d+$")


@dataclass(slots=True)
class DiffLine:
    kind: LineKind
    content: str
    old_line: int | None = None
    new_line: int | None = None

    @property
    def is_added(self) -> bool:
        return self.kind == "add"

    @property
    def is_removed(self) -> bool:
        return self.kind == "remove"


@dataclass(slots=True)
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    heading: str = ""
    lines: list[DiffLine] = field(default_factory=list)
    truncated: bool = False

    @property
    def added_lines(self) -> list[DiffLine]:
        return [line for line in self.lines if line.is_added]

    @property
    def removed_lines(self) -> list[DiffLine]:
        return [line for line in self.lines if line.is_removed]

    @property
    def new_range(self) -> tuple[int, int]:
        """Inclusive ``(start, end)`` of new-side line numbers touched."""
        numbers = [line.new_line for line in self.lines if line.new_line is not None]
        if not numbers:
            return (self.new_start, self.new_start)
        return (min(numbers), max(numbers))


@dataclass(slots=True)
class DiffFile:
    old_path: str | None
    new_path: str | None
    status: FileStatus
    hunks: list[DiffHunk] = field(default_factory=list)
    is_binary: bool = False
    old_mode: str | None = None
    new_mode: str | None = None
    path: str = ""

    @property
    def display_path(self) -> str:
        return self.new_path or self.old_path or self.path

    @property
    def added_line_numbers(self) -> set[int]:
        return {
            line.new_line for hunk in self.hunks for line in hunk.added_lines if line.new_line is not None
        }

    @property
    def commentable_line_numbers(self) -> set[int]:
        """New-side lines GitHub accepts a RIGHT-side comment on.

        Context lines and added lines both appear in the diff and are therefore
        anchorable; removed lines have no position on the new side.
        """
        return {line.new_line for hunk in self.hunks for line in hunk.lines if line.new_line is not None}

    @property
    def added_line_count(self) -> int:
        return sum(1 for hunk in self.hunks for line in hunk.added_lines)

    @property
    def removed_line_count(self) -> int:
        return sum(1 for hunk in self.hunks for line in hunk.removed_lines)

    @property
    def changed_ranges(self) -> list[tuple[int, int]]:
        """Contiguous new-side ranges containing at least one added line."""
        ranges: list[tuple[int, int]] = []
        for hunk in self.hunks:
            current: list[int] = []
            for line in hunk.lines:
                if line.is_added and line.new_line is not None:
                    current.append(line.new_line)
                elif current:
                    ranges.append((current[0], current[-1]))
                    current = []
            if current:
                ranges.append((current[0], current[-1]))
        return ranges


@dataclass(slots=True)
class ParsedDiff:
    files: list[DiffFile]
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.files

    @property
    def changed_files(self) -> list[str]:
        return [file.display_path for file in self.files]

    @property
    def added_lines(self) -> int:
        return sum(file.added_line_count for file in self.files)

    @property
    def removed_lines(self) -> int:
        return sum(file.removed_line_count for file in self.files)

    def file_for(self, path: str) -> DiffFile | None:
        normalised = path.lstrip("./")
        for file in self.files:
            candidates = {item.lstrip("./") for item in (file.new_path, file.old_path, file.path) if item}
            if normalised in candidates:
                return file
        return None

    def commentable_lines(self, path: str) -> set[int]:
        file = self.file_for(path)
        return file.commentable_line_numbers if file else set()

    def added_lines_for(self, path: str) -> set[int]:
        file = self.file_for(path)
        return file.added_line_numbers if file else set()

    def added_line_text(self, path: str, line_number: int) -> str | None:
        file = self.file_for(path)
        if file is None:
            return None
        for hunk in file.hunks:
            for line in hunk.lines:
                if line.is_added and line.new_line == line_number:
                    return line.content
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "files": [
                {
                    "path": file.display_path,
                    "status": file.status,
                    "hunks": len(file.hunks),
                    "added_lines": file.added_line_count,
                    "removed_lines": file.removed_line_count,
                    "binary": file.is_binary,
                }
                for file in self.files
            ],
            "totals": {"files": len(self.files), "added": self.added_lines, "removed": self.removed_lines},
            "warnings": self.warnings[:20],
            "truncated": self.truncated,
        }


class DiffParser:
    """Parses the text of a unified diff."""

    def __init__(self, *, max_hunks_per_file: int = 500, max_files: int = 300) -> None:
        self.max_hunks_per_file = max_hunks_per_file
        self.max_files = max_files

    def parse(self, text: str) -> ParsedDiff:
        if not text or not text.strip():
            return ParsedDiff(files=[], warnings=["diff is empty"])

        lines = text.replace("\r\n", "\n").split("\n")
        parsed = ParsedDiff(files=[])
        current: DiffFile | None = None
        hunk: DiffHunk | None = None
        hunk_complete = False
        overrun_reported = False
        old_line = new_line = 0
        remaining_old = remaining_new = 0
        index = 0

        while index < len(lines):
            raw = lines[index]
            index += 1

            header = _DIFF_HEADER_RE.match(raw)
            if header:
                self._finalise_hunk(current, hunk, parsed)
                hunk = None
                hunk_complete = False
                new_path = _strip_prefix(header.group("new"))
                old_path = _strip_prefix(header.group("old"))
                current = DiffFile(
                    old_path=old_path,
                    new_path=new_path,
                    status="modified",
                    path=new_path or old_path or "",
                )
                if len(parsed.files) >= self.max_files:
                    parsed.truncated = True
                    parsed.warnings.append(f"diff truncated at {self.max_files} files")
                    break
                parsed.files.append(current)
                continue

            if current is None:
                if raw.strip():
                    parsed.warnings.append(f"ignored content before first file header: {raw[:80]!r}")
                continue

            if raw.startswith("@@"):
                self._finalise_hunk(current, hunk, parsed)
                hunk = None
                hunk_complete = False
                match = _HUNK_RE.match(raw)
                if match is None:
                    parsed.warnings.append(f"malformed hunk header in {current.display_path}: {raw[:80]!r}")
                    hunk = None
                    continue
                if len(current.hunks) >= self.max_hunks_per_file:
                    parsed.truncated = True
                    parsed.warnings.append(f"hunk limit reached for {current.display_path}")
                    hunk = None
                    continue
                remaining_old = int(match.group("old_count") or 1)
                remaining_new = int(match.group("new_count") or 1)
                old_line = int(match.group("old_start"))
                new_line = int(match.group("new_start"))
                hunk = DiffHunk(
                    old_start=old_line,
                    old_count=remaining_old,
                    new_start=new_line,
                    new_count=remaining_new,
                    heading=match.group("heading").strip(),
                )
                current.hunks.append(hunk)
                hunk_complete = False
                overrun_reported = False
                continue

            if hunk is None:
                self._apply_file_metadata(current, raw, parsed)
                continue

            if hunk_complete and not _looks_like_hunk_line(raw):
                # A complete hunk followed by something that is not a diff line:
                # metadata belongs to the file, not to the hunk.
                self._finalise_hunk(current, hunk, parsed)
                hunk = None
                hunk_complete = False
                self._apply_file_metadata(current, raw, parsed)
                continue

            if raw.startswith("\\"):  # "\ No newline at end of file"
                continue
            if raw.startswith("+"):
                hunk.lines.append(DiffLine(kind="add", content=raw[1:], new_line=new_line))
                new_line += 1
                remaining_new -= 1
            elif raw.startswith("-"):
                hunk.lines.append(DiffLine(kind="remove", content=raw[1:], old_line=old_line))
                old_line += 1
                remaining_old -= 1
            elif raw.startswith(" ") or raw == "":
                hunk.lines.append(
                    DiffLine(
                        kind="context",
                        content=raw[1:] if raw else "",
                        old_line=old_line,
                        new_line=new_line,
                    )
                )
                old_line += 1
                new_line += 1
                remaining_old -= 1
                remaining_new -= 1
            else:
                parsed.warnings.append(f"unexpected line inside hunk in {current.display_path}: {raw[:80]!r}")
                continue

            if remaining_old <= 0 and remaining_new <= 0:
                if hunk_complete and not overrun_reported:
                    # The header claimed fewer lines than the hunk actually has.
                    # Trusting the header would silently drop the difference, so
                    # keep the lines and say so; ``_finalise_hunk`` marks the hunk
                    # as truncated because the counts now disagree.
                    parsed.warnings.append(
                        f"hunk header in {current.display_path} under-counted its lines; "
                        "the extra lines were kept"
                    )
                    overrun_reported = True
                hunk_complete = True

        self._finalise_hunk(current, hunk, parsed)
        return parsed

    # ----------------------------------------------------------------- internals
    def _finalise_hunk(self, current: DiffFile | None, hunk: DiffHunk | None, parsed: ParsedDiff) -> None:
        if current is None or hunk is None:
            return
        actual_old = sum(1 for line in hunk.lines if line.kind in {"context", "remove"})
        actual_new = sum(1 for line in hunk.lines if line.kind in {"context", "add"})
        if (actual_old, actual_new) != (hunk.old_count, hunk.new_count):
            hunk.truncated = True
            parsed.warnings.append(
                f"hunk line counts disagree with header in {current.display_path}"
                f" (@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count}):"
                f" parsed {actual_old}/{actual_new}"
            )

    def _apply_file_metadata(self, current: DiffFile, raw: str, parsed: ParsedDiff) -> None:
        if raw.startswith("new file mode"):
            current.status = "added"
            current.new_mode = raw.rsplit(" ", 1)[-1]
            return
        if raw.startswith("deleted file mode"):
            current.status = "deleted"
            current.old_mode = raw.rsplit(" ", 1)[-1]
            return
        if raw.startswith("old mode"):
            current.old_mode = raw.rsplit(" ", 1)[-1]
            return
        if raw.startswith("new mode"):
            current.new_mode = raw.rsplit(" ", 1)[-1]
            return
        if raw.startswith("rename from "):
            current.status = "renamed"
            current.old_path = raw[len("rename from ") :].strip()
            return
        if raw.startswith("rename to "):
            current.new_path = raw[len("rename to ") :].strip()
            current.path = current.new_path
            return
        if raw.startswith(("copy from ", "copy to ")):
            current.status = "renamed"
            return
        if raw.startswith(("Binary files ", "GIT binary patch")):
            current.is_binary = True
            current.status = "binary"
            return
        if raw.startswith("--- "):
            path = raw[4:].strip()
            current.old_path = None if path == "/dev/null" else _strip_prefix(path)
            return
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            current.new_path = None if path == "/dev/null" else _strip_prefix(path)
            if current.new_path:
                current.path = current.new_path
            elif current.old_path:
                current.status = "deleted"
            return
        if _INDEX_RE.match(raw) or _MODE_RE.match(raw) or not raw.strip():
            return
        parsed.warnings.append(f"unrecognised diff header line: {raw[:80]!r}")


def _looks_like_hunk_line(raw: str) -> bool:
    """True for anything a unified diff hunk body can contain.

    Used only after a hunk's declared line counts are satisfied: an extra line in
    that position means the header lied, and keeping the line beats dropping real
    content on the floor. A completely empty line is *not* a hunk line here (git
    writes ``" "`` for empty context), so it ends the hunk instead of inventing a
    line beyond the header.
    """
    return bool(raw) and raw[0] in "+- \\"


def _strip_prefix(path: str) -> str | None:
    value = path.strip()
    if value in {"", "/dev/null"}:
        return None
    if value.startswith(("a/", "b/")):
        return value[2:]
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def parse_diff(text: str, *, parser: DiffParser | None = None) -> ParsedDiff:
    """Convenience wrapper."""
    return (parser or DiffParser()).parse(text)


def new_side_lines(file: DiffFile) -> list[str]:
    """Every new-side line the diff shows for ``file`` (context and added)."""
    lines: list[str] = []
    for hunk in file.hunks:
        lines.extend(line.content for line in hunk.lines if not line.is_removed)
    return lines


def new_side_matches(file: DiffFile, text: str) -> bool:
    """True when ``text`` already equals the diff's new side at every hunk.

    A checkout at the pull request head satisfies this; a checkout at the base
    revision does not, because the added lines are not there yet.
    """
    candidate = text.splitlines()
    for hunk in file.hunks:
        start = hunk.new_start - 1 if hunk.new_count else hunk.new_start
        expected = [line.content for line in hunk.lines if not line.is_removed]
        if candidate[start : start + len(expected)] != expected:
            return False
    return True


def apply_hunks_to_text(file: DiffFile, text: str) -> str | None:
    """Reconstruct the new side by applying the diff's hunks to old-side ``text``.

    Returns ``None`` when a hunk does not apply, i.e. the text is neither the old
    side nor the new side of this diff. That distinction matters: silently running
    AST checks against unrelated content would produce confident nonsense.
    """
    lines = text.splitlines()
    for hunk in sorted(file.hunks, key=lambda item: item.old_start, reverse=True):
        old_lines = [line.content for line in hunk.lines if not line.is_added]
        new_lines = [line.content for line in hunk.lines if not line.is_removed]
        start = hunk.old_start - 1 if hunk.old_count else hunk.old_start
        if hunk.old_count and lines[start : start + hunk.old_count] != old_lines:
            return None
        lines[start : start + hunk.old_count] = new_lines
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def render_added_excerpt(parsed: ParsedDiff, *, max_chars: int = 6_000, max_lines: int = 200) -> str:
    """Render added/removed lines as a compact excerpt for a model prompt.

    Deliberately excludes context lines and whole file bodies: the model should
    see what changed, not an entire repository file.
    """
    chunks: list[str] = []
    emitted = 0
    for file in parsed.files:
        lines: list[str] = [
            f"### {file.display_path} ({file.status}, +{file.added_line_count}/-{file.removed_line_count})"
        ]
        for hunk in file.hunks:
            heading = f" {hunk.heading}" if hunk.heading else ""
            lines.append(f"@@ -{hunk.old_start} +{hunk.new_start} @@{heading}")
            for line in hunk.lines:
                if line.kind == "context":
                    continue
                marker = "+" if line.is_added else "-"
                number = line.new_line if line.is_added else line.old_line
                lines.append(f"{marker}{number}: {line.content}")
                emitted += 1
                if emitted >= max_lines:
                    break
            if emitted >= max_lines:
                break
        chunks.append("\n".join(lines))
        if emitted >= max_lines:
            break

    text = "\n\n".join(chunks)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [diff excerpt truncated]"
    return text


__all__ = [
    "DiffFile",
    "DiffHunk",
    "DiffLine",
    "DiffParser",
    "ParsedDiff",
    "apply_hunks_to_text",
    "new_side_lines",
    "new_side_matches",
    "parse_diff",
    "render_added_excerpt",
]
