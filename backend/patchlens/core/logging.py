"""Structured logging with secret redaction.

Two rules drive this module:

1. Repository content, tokens and prompts are untrusted/sensitive: log
   *metadata*, never whole file bodies, credentials or installation tokens.
2. A log line must be machine-parseable in production (JSON) and readable in
   development (compact key=value).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from contextvars import ContextVar
from typing import Any

from patchlens.core.config import get_settings

_request_id: ContextVar[str | None] = ContextVar("patchlens_request_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("patchlens_job_id", default=None)

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(gh[pousr]_[A-Za-z0-9]{16,})"), "gh*_***REDACTED***"),
    (re.compile(r"(github_pat_[A-Za-z0-9_]{20,})"), "github_pat_***REDACTED***"),
    (re.compile(r"(sk-[A-Za-z0-9_-]{16,})"), "sk-***REDACTED***"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "***PRIVATE_KEY_REDACTED***",
    ),
    (
        re.compile(
            r"(?i)\b(authorization|api[_-]?key|token|secret|password|passwd)\b(\s*[:=]\s*)(\"?[^\s\"',}]{6,}\"?)(?!\w)"
        ),
        r"\1\2***REDACTED***",
    ),
    (re.compile(r"://([^/\s:@]+):([^/\s@]+)@"), r"://\1:***REDACTED***@"),
)

#: Values that are obviously not secrets and must stay readable in logs.
_REDACTION_ALLOWLIST = {"", "null", "none", "true", "false", "not-set", "unset", "***REDACTED***"}


def redact(text: object) -> str:
    """Return ``text`` with credential-like substrings masked."""
    value = str(text)
    if len(value) > 20_000:  # avoid pathological regex work on huge payloads
        value = value[:20_000] + "...[truncated]"
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def bind_request_id(request_id: str | None) -> None:
    _request_id.set(request_id)


def bind_job_id(job_id: str | None) -> None:
    _job_id.set(job_id)


def current_request_id() -> str | None:
    return _request_id.get()


def current_job_id() -> str | None:
    return _job_id.get()


def describe_exception(exc: BaseException, *, max_frames: int = 6) -> dict[str, Any]:
    """Compact, redacted exception payload safe for logs and job records."""
    import traceback

    frames = traceback.extract_tb(exc.__traceback__)[-max_frames:]
    return {
        "type": type(exc).__name__,
        "message": redact(exc),
        "frames": [f"{frame.filename.split('/')[-1]}:{frame.lineno}:{frame.name}" for frame in frames],
    }


class _Formatter(logging.Formatter):
    def __init__(self, *, as_json: bool) -> None:
        super().__init__()
        self._as_json = as_json

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        request_id = _request_id.get()
        job_id = _job_id.get()
        if request_id:
            payload["request_id"] = request_id
        if job_id:
            payload["job_id"] = job_id

        extras = getattr(record, "extra_fields", None)
        if isinstance(extras, dict):
            for key, value in extras.items():
                payload[key] = redact(value) if isinstance(value, str) else value

        if record.exc_info and record.exc_info[1] is not None:
            payload["error"] = describe_exception(record.exc_info[1])

        if self._as_json:
            return json.dumps(payload, default=str)

        head = f"{payload['ts']} {payload['level']:<7} {payload['logger']}"
        if request_id:
            head += f" req={request_id[:8]}"
        if job_id:
            head += f" job={job_id[:8]}"
        tail = " ".join(
            f"{key}={value}" for key, value in payload.items() if key not in {"ts", "level", "logger", "msg"}
        )
        line = f"{head} | {payload['msg']}"
        return f"{line} | {tail}" if tail else line


_configured = False


def configure_logging(*, level: str | None = None, as_json: bool | None = None, force: bool = False) -> None:
    """Install the PatchLens handler on the root logger (idempotent)."""
    global _configured
    if _configured and not force:
        return
    settings = get_settings()
    resolved_level = (level or settings.log_level).upper()
    resolved_json = settings.log_json if as_json is None else as_json

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(_Formatter(as_json=resolved_json))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved_level)

    # Third-party loggers are noisy and may echo request payloads.
    for noisy, noisy_level in (
        ("httpx", "WARNING"),
        ("httpcore", "WARNING"),
        ("uvicorn.access", "WARNING"),
        ("sqlalchemy.engine", "WARNING"),
        ("alembic", "INFO"),
    ):
        logging.getLogger(noisy).setLevel(noisy_level)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_extra(**fields: Any) -> dict[str, Any]:
    """Build the ``extra`` dict consumed by :class:`_Formatter`."""
    return {"extra_fields": fields}
