"""FastAPI application factory.

Middleware order matters and is deliberate:

1. request-id/context + structured access log (outermost, so every error is logged
   with its request id);
2. body-size guard (rejects oversized payloads before they are parsed);
3. CORS (browser access);
4. routers.

Schema creation at startup is a development/test convenience only; production
deployments run ``alembic upgrade head`` and the app logs which mode is active.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from patchlens import __version__
from patchlens.api.routes import api_router
from patchlens.core.config import Settings, get_settings
from patchlens.core.errors import PayloadTooLargeError, register_exception_handlers
from patchlens.core.logging import bind_request_id, configure_logging, get_logger, log_extra
from patchlens.db.session import create_all, get_database

logger = get_logger(__name__)

REQUEST_ID_HEADER = "x-request-id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id, log the outcome and add timing headers."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        bind_request_id(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.exception(
                "unhandled request error",
                extra=log_extra(method=request.method, path=request.url.path, duration_ms=duration_ms),
            )
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["x-response-time-ms"] = str(duration_ms)
        log = logger.warning if response.status_code >= 500 else logger.info
        log(
            "request",
            extra=log_extra(
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
            ),
        )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose declared body exceeds the configured limit."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        header = request.headers.get("content-length")
        if header is not None:
            try:
                declared = int(header)
            except ValueError:
                declared = 0
            if declared > self.max_bytes:
                # Answered here rather than raised: exceptions from middleware run
                # outside the router, so FastAPI's handlers would never see it and
                # the client would get a generic 500 instead of this payload.
                error = PayloadTooLargeError(
                    f"request body of {declared} bytes exceeds the {self.max_bytes}-byte limit",
                    details={"max_bytes": self.max_bytes},
                )
                logger.warning(
                    "payload rejected before parsing",
                    extra=log_extra(path=request.url.path, declared_bytes=declared, max_bytes=self.max_bytes),
                )
                return JSONResponse(status_code=error.status_code, content=error.to_payload())
        return await call_next(request)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, as_json=settings.log_json)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Schema/startup handling; replaced the deprecated on_event hooks."""
        _ensure_schema(settings)
        try:
            yield
        finally:
            dispose_database()
            logger.info("application stopped")

    app = FastAPI(
        lifespan=lifespan,
        title=settings.app_name,
        version=__version__,
        description=(
            "Repository-aware code review and test intelligence. Deterministic checks, hybrid "
            "retrieval and a configurable LLM provider; every finding records how it was produced."
        ),
        docs_url="/docs" if settings.api_docs_enabled else None,
        redoc_url="/redoc" if settings.api_docs_enabled else None,
        openapi_url="/openapi.json" if settings.api_docs_enabled else None,
    )
    app.state.settings = settings

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER, "x-response-time-ms"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "health": "/healthz",
            "readiness": "/readyz",
            "api": "/api/v1/meta",
        }

    return app


def _ensure_schema(settings: Settings) -> None:
    """Create tables when the deployment allows it; otherwise defer to Alembic."""
    logger.info("application starting", extra=log_extra(**settings.summary()))
    if settings.auto_create_schema and not settings.is_production:
        database = get_database()
        create_all(database)
        logger.info(
            "schema ensured via create_all (development convenience)",
            extra=log_extra(dialect=database.dialect, auto_create_schema=True),
        )
    else:
        logger.info(
            "schema management is delegated to alembic; run 'alembic upgrade head'",
            extra=log_extra(auto_create_schema=settings.auto_create_schema),
        )


@contextmanager
def lifespan_context(settings: Settings) -> Iterator[None]:
    """Startup/shutdown used by the ASGI lifespan and by tests."""
    _ensure_schema(settings)
    try:
        yield
    finally:
        dispose_database()
        logger.info("application stopped")


def dispose_database() -> None:
    get_database().dispose()


app = create_app

__all__ = [
    "BodySizeLimitMiddleware",
    "RequestContextMiddleware",
    "app",
    "create_app",
    "dispose_database",
    "lifespan_context",
]
