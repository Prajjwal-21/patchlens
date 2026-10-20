"""Added-line checks.

These run on every diff because they need only the diff text itself - no file
content, no AST. They are the checks that still work when PatchLens has no indexed
snapshot for a repository.
"""

from __future__ import annotations

import re

from patchlens.core.enums import FindingCategory, Severity, VerificationStatus
from patchlens.core.security import is_secret_like_name, looks_like_placeholder_value
from patchlens.services.review.checks_common import (
    CheckContext,
    CheckFunction,
    CheckSpec,
    build_finding,
    indentation,
    line_evidence,
)
from patchlens.services.review.types import FindingDraft

_BARE_EXCEPT_RE = re.compile(r"^\s*except\s*:\s*(#.*)?$")
_EXCEPT_RE = re.compile(r"^\s*except\b(?P<rest>.*):\s*(#.*)?$")
_BROAD_EXCEPT_RE = re.compile(
    r"^\s*except\s*\(?\s*(BaseException|Exception|BaseException\s*,\s*Exception)\s*\)?\s*(as\s+\w+)?\s*:"
)
_SWALLOW_RE = re.compile(r"^\s*(pass|return(\s+None)?)\s*(#.*)?$")
_LOG_RE = re.compile(r"\b(log|logger|logging|warn|warning|error|exception|critical|print)\b")
_EVAL_RE = re.compile(r"(?<![\w.])(eval|exec)\s*\(")
_SHELL_TRUE_RE = re.compile(r"\bshell\s*=\s*True\b")
_OS_SYSTEM_RE = re.compile(r"(?<![\w.])os\.system\s*\(")
_SQL_EXEC_RE = re.compile(r"(?<![\w])(?:\w+\.)?(?:execute|executemany|executescript)\s*\(")
_SQL_INTERP_RE = re.compile(r"(f[\"']|\"%s?['\"]|'%s?['\"]|\.\s*format\s*\(|\+\s*\w)")
_YAML_LOAD_RE = re.compile(r"(?<![\w.])yaml\.load\s*\(")
_PICKLE_RE = re.compile(r"(?<![\w.])(pickle|cPickle|dill|marshal)\.loads?\s*\(")
_WEAK_HASH_RE = re.compile(r"hashlib\.(md5|sha1)\s*\(")
_UTCNOW_RE = re.compile(r"datetime\.utcnow\s*\(\s*\)")
_NAIVE_NOW_RE = re.compile(r"datetime\.now\s*\(\s*\)(?!\s*,)")
_MKTEMP_RE = re.compile(r"tempfile\.mktemp\s*\(")
_HTTP_CALL_RE = re.compile(
    r"(?<![\w.])(requests|httpx|aiohttp|urllib3)\.(get|post|put|patch|delete|head|options|request)\b"
)
_BREAKPOINT_RE = re.compile(r"(?<![\w.])(breakpoint\s*\(|pdb\.set_trace\s*\(|import\s+pdb\b)")
_SECRET_ASSIGN_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=\s*(?P<value>[\"'])(?P<literal>[^\"']{8,})(?P=value)\s*$"
)
_ASSERT_RE = re.compile(r"^\s*assert\s+\S")
_MUTABLE_DEFAULT_RE = re.compile(r"def\s+\w+\s*\([^)]*=\s*(\[\]|\{\}|dict\(\)|list\(\)|set\(\))")


SPEC_BARE_EXCEPT = CheckSpec(
    rule_id="bare-except",
    category=FindingCategory.CORRECTNESS,
    severity=Severity.HIGH,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=1.0,
    summary="bare `except:` catches every exception, including SystemExit and KeyboardInterrupt",
    remediation="Catch the specific exception types you can handle, e.g. `except ValueError:`.",
)

SPEC_EXCEPT_SUPPRESSES = CheckSpec(
    rule_id="except-suppresses-error",
    category=FindingCategory.RELIABILITY,
    severity=Severity.HIGH,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=0.9,
    summary="exception handler discards the error without logging or re-raising",
    remediation="Log the exception with context, re-raise it, or narrow the handled type.",
)

SPEC_BROAD_EXCEPT_SWALLOW = CheckSpec(
    rule_id="broad-except-swallow",
    category=FindingCategory.RELIABILITY,
    severity=Severity.MEDIUM,
    verification=VerificationStatus.NEEDS_MANUAL_VERIFICATION,
    detector_confidence=0.6,
    summary="`except Exception` silently continues, hiding programming errors",
    remediation="Handle the expected exception types; let unexpected ones propagate or record them.",
)

SPEC_EVAL_EXEC = CheckSpec(
    rule_id="eval-exec-usage",
    category=FindingCategory.SECURITY,
    severity=Severity.CRITICAL,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=1.0,
    summary="`eval`/`exec` execute arbitrary code from a string",
    remediation="Replace with a parser or an explicit dispatch table (e.g. `ast.literal_eval` for data).",
)

SPEC_SHELL_TRUE = CheckSpec(
    rule_id="subprocess-shell-true",
    category=FindingCategory.SECURITY,
    severity=Severity.HIGH,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=1.0,
    summary="`shell=True` runs the command through a shell, enabling argument injection",
    remediation="Pass an argument list with `shell=False`, or validate against a strict allowlist.",
)

SPEC_OS_SYSTEM = CheckSpec(
    rule_id="os-system-usage",
    category=FindingCategory.SECURITY,
    severity=Severity.HIGH,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=1.0,
    summary="`os.system` invokes the platform shell with a single string",
    remediation="Use `subprocess.run([...])` with an explicit argument list.",
)

SPEC_SQL_FORMATTING = CheckSpec(
    rule_id="sql-string-interpolation",
    category=FindingCategory.SECURITY,
    severity=Severity.HIGH,
    verification=VerificationStatus.NEEDS_MANUAL_VERIFICATION,
    detector_confidence=0.55,
    summary="SQL built by string interpolation can be injectable",
    remediation="Use bound parameters (e.g. `execute('... WHERE id = :id', {'id': value})`).",
)

SPEC_YAML_LOAD = CheckSpec(
    rule_id="unsafe-yaml-load",
    category=FindingCategory.SECURITY,
    severity=Severity.HIGH,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=0.9,
    summary="`yaml.load` without an explicit Loader can construct arbitrary objects",
    remediation="Use `yaml.safe_load(...)` or pass `Loader=yaml.SafeLoader`.",
)

SPEC_PICKLE = CheckSpec(
    rule_id="unsafe-deserialization",
    category=FindingCategory.SECURITY,
    severity=Severity.MEDIUM,
    verification=VerificationStatus.NEEDS_MANUAL_VERIFICATION,
    detector_confidence=0.6,
    summary="deserialising pickle-compatible payloads can execute arbitrary code",
    remediation="Use a data-only format (JSON) for untrusted input, or verify the payload's signature first.",
)

SPEC_WEAK_HASH = CheckSpec(
    rule_id="weak-hash-algorithm",
    category=FindingCategory.SECURITY,
    severity=Severity.LOW,
    verification=VerificationStatus.NEEDS_MANUAL_VERIFICATION,
    detector_confidence=0.5,
    summary="MD5/SHA-1 collisions are practical; they are unsuitable for integrity or signatures",
    remediation="Use SHA-256 or stronger where the hash protects a security property.",
)

SPEC_NAIVE_DATETIME = CheckSpec(
    rule_id="naive-datetime",
    category=FindingCategory.RELIABILITY,
    severity=Severity.LOW,
    verification=VerificationStatus.NEEDS_MANUAL_VERIFICATION,
    detector_confidence=0.6,
    summary="naive local timestamps are ambiguous across timezones and DST",
    remediation="Use timezone-aware values: `datetime.now(timezone.utc)`.",
)

SPEC_UTC_NOW_DEPRECATED = CheckSpec(
    rule_id="datetime-utcnow-deprecated",
    category=FindingCategory.MAINTAINABILITY,
    severity=Severity.LOW,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=0.9,
    summary="`datetime.utcnow()` returns a naive value and is deprecated in Python 3.12+",
    remediation="Use `datetime.now(datetime.UTC)`.",
)

SPEC_MKTEMP = CheckSpec(
    rule_id="insecure-tempfile",
    category=FindingCategory.SECURITY,
    severity=Severity.MEDIUM,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=0.9,
    summary="`tempfile.mktemp` is racy: the name can be taken between check and use",
    remediation="Use `tempfile.NamedTemporaryFile`/`mkstemp`, which return an open handle.",
)

SPEC_HTTP_NO_TIMEOUT = CheckSpec(
    rule_id="http-call-without-timeout",
    category=FindingCategory.RELIABILITY,
    severity=Severity.MEDIUM,
    verification=VerificationStatus.NEEDS_MANUAL_VERIFICATION,
    detector_confidence=0.6,
    summary="HTTP calls without a timeout can block a worker indefinitely",
    remediation="Always pass an explicit `timeout=` on outbound calls.",
)

SPEC_HARDCODED_SECRET = CheckSpec(
    rule_id="hardcoded-secret",
    category=FindingCategory.SECURITY,
    severity=Severity.HIGH,
    verification=VerificationStatus.NEEDS_MANUAL_VERIFICATION,
    detector_confidence=0.55,
    summary="credential-like name assigned a string literal",
    remediation="Load secrets from the environment or a secret manager; rotate anything already committed.",
)

SPEC_ASSERT_LIBRARY = CheckSpec(
    rule_id="assert-in-library-code",
    category=FindingCategory.CORRECTNESS,
    severity=Severity.MEDIUM,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=0.75,
    summary="`assert` statements are removed by `python -O`, so they cannot enforce runtime contracts",
    remediation="Raise an explicit exception (e.g. `ValueError`) for conditions that must hold.",
)

SPEC_MUTABLE_DEFAULT = CheckSpec(
    rule_id="mutable-default-argument",
    category=FindingCategory.CORRECTNESS,
    severity=Severity.HIGH,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=0.95,
    summary="mutable default arguments are created once and shared across calls",
    remediation="Default to `None` and construct the container inside the function body.",
)

SPEC_BREAKPOINT = CheckSpec(
    rule_id="debug-breakpoint",
    category=FindingCategory.MAINTAINABILITY,
    severity=Severity.LOW,
    verification=VerificationStatus.VERIFIED,
    detector_confidence=0.95,
    summary="interactive debugger hook left in the change",
    remediation="Remove the breakpoint/pdb call before merging.",
)


def check_bare_except(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path, line_number, content in context.added_lines():
        if not _BARE_EXCEPT_RE.match(content):
            continue
        findings.append(
            build_finding(
                SPEC_BARE_EXCEPT,
                path=path,
                start_line=line_number,
                title="Bare `except:` added",
                explanation=(
                    "A bare `except:` handler catches every exception raised in its `try` block, including"
                    " `KeyboardInterrupt` and `SystemExit`, and hides genuine bugs because no exception type"
                    f" is named. The added line {line_number} in {path} introduces one."
                ),
                evidence=[line_evidence(path, line_number, content)],
            )
        )
    return findings


def check_except_suppresses(context: CheckContext) -> list[FindingDraft]:
    """`except X:` followed by `pass`/bare return with no logging nearby."""
    findings: list[FindingDraft] = []
    added_by_path: dict[str, list[tuple[int, str]]] = {}
    for path, line_number, content in context.added_lines():
        added_by_path.setdefault(path, []).append((line_number, content))

    for path, lines in added_by_path.items():
        for index, (line_number, content) in enumerate(lines):
            if not _EXCEPT_RE.match(content):
                continue
            except_indent = indentation(content)
            body_lines = lines[index + 1 : index + 4]
            if not body_lines:
                continue
            body_line_number, body_content = body_lines[0]
            if indentation(body_content) <= except_indent:
                continue
            if not _SWALLOW_RE.match(body_content):
                continue
            nearby = " ".join(text for _number, text in body_lines[:3])
            if _LOG_RE.search(nearby):
                continue
            if _BARE_EXCEPT_RE.match(content):
                continue  # reported by the dedicated bare-except check
            spec = SPEC_BROAD_EXCEPT_SWALLOW if _BROAD_EXCEPT_RE.match(content) else SPEC_EXCEPT_SUPPRESSES
            findings.append(
                build_finding(
                    spec,
                    path=path,
                    start_line=line_number,
                    end_line=body_line_number,
                    title="Exception swallowed without a trace",
                    explanation=(
                        f"The handler added at {path}:{line_number} discards the failure"
                        f" (`{body_content.strip()}`) and neither logs nor re-raises it, so the caller cannot"
                        " tell the operation silently did nothing."
                    ),
                    evidence=[
                        line_evidence(path, line_number, content),
                        line_evidence(path, body_line_number, body_content),
                    ],
                )
            )
    return findings


def check_dangerous_calls(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    patterns = (
        (_EVAL_RE, SPEC_EVAL_EXEC, "Dynamic code execution added"),
        (_SHELL_TRUE_RE, SPEC_SHELL_TRUE, "Subprocess invoked through a shell"),
        (_OS_SYSTEM_RE, SPEC_OS_SYSTEM, "Shell invocation via os.system"),
        (_MKTEMP_RE, SPEC_MKTEMP, "Race-prone temporary file name"),
        (_BREAKPOINT_RE, SPEC_BREAKPOINT, "Debugger hook added"),
    )
    for path, line_number, content in context.added_lines():
        if content.lstrip().startswith("#"):
            continue
        for pattern, spec, title in patterns:
            if not pattern.search(content):
                continue
            findings.append(
                build_finding(
                    spec,
                    path=path,
                    start_line=line_number,
                    title=title,
                    explanation=(
                        f"`{spec.summary}`. Matched on the added line {line_number}:"
                        f" `{content.strip()[:160]}`."
                    ),
                    evidence=[line_evidence(path, line_number, content)],
                )
            )
    return findings


def check_sql_interpolation(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path, line_number, content in context.added_lines():
        if not _SQL_EXEC_RE.search(content):
            continue
        if not _SQL_INTERP_RE.search(content):
            continue
        findings.append(
            build_finding(
                SPEC_SQL_FORMATTING,
                path=path,
                start_line=line_number,
                title="SQL statement assembled with string interpolation",
                explanation=(
                    f"Line {line_number} executes a statement whose text is built by interpolation. If any"
                    " interpolated value comes from user input the query is injectable; confirming the data"
                    " flow needs a human, so this is flagged for manual verification rather than asserted."
                ),
                evidence=[line_evidence(path, line_number, content)],
            )
        )
    return findings


def check_yaml_and_pickle(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path, line_number, content in context.added_lines():
        if _YAML_LOAD_RE.search(content) and "Loader" not in content and "safe_load" not in content:
            findings.append(
                build_finding(
                    SPEC_YAML_LOAD,
                    path=path,
                    start_line=line_number,
                    title="yaml.load without an explicit safe Loader",
                    explanation=(
                        f"`{SPEC_YAML_LOAD.summary}`. Line {line_number} calls `yaml.load` with no `Loader=`"
                        " argument, which defaults to the full (unsafe) loader in PyYAML."
                    ),
                    evidence=[line_evidence(path, line_number, content)],
                )
            )
        if _PICKLE_RE.search(content):
            findings.append(
                build_finding(
                    SPEC_PICKLE,
                    path=path,
                    start_line=line_number,
                    title="Deserialisation of a pickle-compatible payload",
                    explanation=(
                        f"`{SPEC_PICKLE.summary}`. Line {line_number} deserialises bytes; if the payload can be"
                        " influenced by an attacker this is remote code execution."
                    ),
                    evidence=[line_evidence(path, line_number, content)],
                )
            )
    return findings


def check_crypto_and_time(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path, line_number, content in context.added_lines():
        if _WEAK_HASH_RE.search(content):
            findings.append(
                build_finding(
                    SPEC_WEAK_HASH,
                    path=path,
                    start_line=line_number,
                    title="Weak hash algorithm used",
                    explanation=(
                        f"`{SPEC_WEAK_HASH.summary}`. Line {line_number} constructs an MD5/SHA-1 hash; whether"
                        " that matters depends on its purpose (cache key vs. signature)."
                    ),
                    evidence=[line_evidence(path, line_number, content)],
                )
            )
        if _UTCNOW_RE.search(content):
            findings.append(
                build_finding(
                    SPEC_UTC_NOW_DEPRECATED,
                    path=path,
                    start_line=line_number,
                    title="`datetime.utcnow()` returns a naive timestamp",
                    explanation=(
                        f"`{SPEC_UTC_NOW_DEPRECATED.summary}`. Line {line_number} uses it; the resulting object"
                        " carries no timezone and compares incorrectly against aware datetimes."
                    ),
                    evidence=[line_evidence(path, line_number, content)],
                )
            )
        elif _NAIVE_NOW_RE.search(content) and "tz" not in content:
            findings.append(
                build_finding(
                    SPEC_NAIVE_DATETIME,
                    path=path,
                    start_line=line_number,
                    title="Naive local timestamp created",
                    explanation=(
                        f"`{SPEC_NAIVE_DATETIME.summary}`. Line {line_number} reads the local clock with no"
                        " timezone attached."
                    ),
                    evidence=[line_evidence(path, line_number, content)],
                )
            )
    return findings


def check_http_without_timeout(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path, line_number, content in context.added_lines():
        if path in context.file_texts:
            continue  # the AST check resolves multi-line calls precisely
        if not _HTTP_CALL_RE.search(content):
            continue
        if "timeout" in content:
            continue
        findings.append(
            build_finding(
                SPEC_HTTP_NO_TIMEOUT,
                path=path,
                start_line=line_number,
                title="Outbound HTTP call without a timeout",
                explanation=(
                    f"`{SPEC_HTTP_NO_TIMEOUT.summary}`. Library defaults are effectively unbounded, so a hung"
                    f" peer can stall this path. Line {line_number}: `{content.strip()[:160]}`."
                ),
                evidence=[line_evidence(path, line_number, content)],
            )
        )
    return findings


def check_hardcoded_secret(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path, line_number, content in context.added_lines():
        match = _SECRET_ASSIGN_RE.match(content)
        if not match:
            continue
        name = match.group("name")
        literal = match.group("literal")
        if not is_secret_like_name(name):
            continue
        if looks_like_placeholder_value(literal) or not _looks_like_credential(literal):
            continue
        findings.append(
            build_finding(
                SPEC_HARDCODED_SECRET,
                path=path,
                start_line=line_number,
                title=f"Possible credential literal assigned to `{name}`",
                explanation=(
                    f"`{SPEC_HARDCODED_SECRET.summary}`: `{name}` at {path}:{line_number} is assigned a"
                    f" {len(literal)}-character string literal. PatchLens redacts the value and cannot tell"
                    " whether it is a real credential or a fixture, so it needs verification - and the value"
                    " should be rotated if it was ever valid."
                ),
                evidence=[
                    line_evidence(
                        path,
                        line_number,
                        f"{name} = '{literal[:2]}...[redacted, {len(literal)} chars]'",
                    )
                ],
                suggestion=(
                    "Read the value from configuration at runtime, e.g."
                    f' `{name} = os.environ["{name.upper()}"]`, and list the variable in `.env.example`'
                    " with a placeholder."
                ),
            )
        )
    return findings


def _looks_like_credential(literal: str) -> bool:
    """Shape test: length plus mixed character classes, minus obvious non-secrets."""
    if len(literal) < 12:
        return False
    if literal.startswith(("http://", "https://")):
        return False
    classes = sum(
        (
            any(char.islower() for char in literal),
            any(char.isupper() for char in literal),
            any(char.isdigit() for char in literal),
            any(not char.isalnum() for char in literal),
        )
    )
    return classes >= 3


def check_assert_in_library(context: CheckContext) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for path, line_number, content in context.added_lines():
        if context.is_test_path(path):
            continue
        if not _ASSERT_RE.match(content):
            continue
        findings.append(
            build_finding(
                SPEC_ASSERT_LIBRARY,
                path=path,
                start_line=line_number,
                title="Runtime contract enforced with `assert`",
                explanation=(
                    f"`{SPEC_ASSERT_LIBRARY.summary}`. Line {line_number} in the non-test module {path} asserts"
                    " a condition that callers rely on."
                ),
                evidence=[line_evidence(path, line_number, content)],
            )
        )
    return findings


def check_mutable_default_line(context: CheckContext) -> list[FindingDraft]:
    """Line-level fallback for files whose AST is unavailable."""
    findings: list[FindingDraft] = []
    for path, line_number, content in context.added_lines():
        if path in context.file_texts:
            continue  # the AST check reports this precisely, with the argument name
        match = _MUTABLE_DEFAULT_RE.search(content)
        if not match:
            continue
        findings.append(
            build_finding(
                SPEC_MUTABLE_DEFAULT,
                path=path,
                start_line=line_number,
                title="Mutable default argument in signature",
                explanation=(
                    f"`{SPEC_MUTABLE_DEFAULT.summary}` - the default `{match.group(1)}` on line {line_number}"
                    " is created once and shared by every call, so mutations persist between calls."
                ),
                evidence=[line_evidence(path, line_number, content)],
            )
        )
    return findings


#: Each function runs once per review; it may emit findings for several specs.
LINE_CHECKS: list[CheckFunction] = [
    check_bare_except,
    check_except_suppresses,
    check_dangerous_calls,
    check_sql_interpolation,
    check_yaml_and_pickle,
    check_crypto_and_time,
    check_http_without_timeout,
    check_hardcoded_secret,
    check_assert_in_library,
    check_mutable_default_line,
]

#: Documentation catalogue of every rule implemented in this module.
LINE_SPECS: list[CheckSpec] = [
    SPEC_BARE_EXCEPT,
    SPEC_EXCEPT_SUPPRESSES,
    SPEC_BROAD_EXCEPT_SWALLOW,
    SPEC_EVAL_EXEC,
    SPEC_SHELL_TRUE,
    SPEC_OS_SYSTEM,
    SPEC_SQL_FORMATTING,
    SPEC_YAML_LOAD,
    SPEC_PICKLE,
    SPEC_WEAK_HASH,
    SPEC_NAIVE_DATETIME,
    SPEC_UTC_NOW_DEPRECATED,
    SPEC_MKTEMP,
    SPEC_HTTP_NO_TIMEOUT,
    SPEC_HARDCODED_SECRET,
    SPEC_ASSERT_LIBRARY,
    SPEC_MUTABLE_DEFAULT,
    SPEC_BREAKPOINT,
]

__all__ = [
    "LINE_CHECKS",
    "LINE_SPECS",
    "SPEC_ASSERT_LIBRARY",
    "SPEC_BARE_EXCEPT",
    "SPEC_BREAKPOINT",
    "SPEC_BROAD_EXCEPT_SWALLOW",
    "SPEC_EVAL_EXEC",
    "SPEC_EXCEPT_SUPPRESSES",
    "SPEC_HARDCODED_SECRET",
    "SPEC_HTTP_NO_TIMEOUT",
    "SPEC_MKTEMP",
    "SPEC_MUTABLE_DEFAULT",
    "SPEC_NAIVE_DATETIME",
    "SPEC_OS_SYSTEM",
    "SPEC_PICKLE",
    "SPEC_SHELL_TRUE",
    "SPEC_SQL_FORMATTING",
    "SPEC_UTC_NOW_DEPRECATED",
    "SPEC_WEAK_HASH",
    "SPEC_YAML_LOAD",
    "check_assert_in_library",
    "check_bare_except",
    "check_crypto_and_time",
    "check_dangerous_calls",
    "check_except_suppresses",
    "check_hardcoded_secret",
    "check_http_without_timeout",
    "check_mutable_default_line",
    "check_sql_interpolation",
    "check_yaml_and_pickle",
]
