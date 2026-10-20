"""Typed application errors and the FastAPI exception mapping.

Routes raise these; a single handler converts them to RFC7807-ish JSON bodies.
Business logic never raises bare ``Exception`` and never swallows errors
silently: unexpected failures become ``InternalError`` with a logged cause.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from patchlens.core.logging import describe_exception, get_logger, log_extra

logger = get_logger(__name__)


class PatchLensError(Exception):
    """Base class for all expected application errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class NotFoundError(PatchLensError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ValidationError(PatchLensError):
    #: Numeric literals rather than Starlette constants: the constant names were
    #: renamed across Starlette releases, the codes never change.
    status_code = 422
    code = "validation_error"


class ConflictError(PatchLensError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class AuthError(PatchLensError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(PatchLensError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class PayloadTooLargeError(PatchLensError):
    status_code = 413
    code = "payload_too_large"


class RateLimitedError(PatchLensError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class ProviderError(PatchLensError):
    """A model/embedding/GitHub provider failed in a retryable way."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "provider_error"


class ProviderNotConfiguredError(ProviderError):
    code = "provider_not_configured"


class SandboxUnavailableError(PatchLensError):
    status_code = status.HTTP_409_CONFLICT
    code = "sandbox_unavailable"


class JobError(PatchLensError):
    """A job handler failed; the queue decides whether to retry."""

    code = "job_error"

    def __init__(
        self, message: str, *, retryable: bool = True, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, details=details)
        self.retryable = retryable


#: Frameworks raise bare ``HTTPException``s for routing-level problems; give them
#: the same vocabulary as the application's own errors.
_HTTP_ERROR_CODES: dict[int, str] = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach JSON error handlers; keeps route handlers free of try/except soup."""

    @app.exception_handler(PatchLensError)
    async def _handle_patchlens(request: Request, exc: PatchLensError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error(
                "application error",
                extra=log_extra(path=request.url.path, **describe_exception(exc)),
            )
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "request body or parameters failed validation",
                    "details": {"errors": _safe_errors(exc.errors())},
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_ERROR_CODES.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": str(exc.detail), "details": {}}},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error", extra=log_extra(path=request.url.path, **describe_exception(exc)))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "internal server error",
                    "details": {},
                }
            },
        )


def _safe_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Strip non-serialisable and sensitive values from pydantic error details."""
    safe: list[dict[str, Any]] = []
    for error in errors[:20]:
        safe.append(
            {
                "loc": [str(part) for part in error.get("loc", [])],
                "type": error.get("type"),
                "msg": str(error.get("msg", ""))[:300],
            }
        )
    return safe
