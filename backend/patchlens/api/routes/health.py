"""Health, readiness and runtime metadata endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from patchlens import __version__
from patchlens.api.deps import read_session, settings_dependency
from patchlens.api.schemas import HealthDetailResponse, HealthResponse, MetaResponse
from patchlens.api.serializers import METADATA_KEEP  # noqa: F401 - imported for docs symmetry
from patchlens.core.config import Settings
from patchlens.core.enums import FeedbackLabel, RetrievalStrategy
from patchlens.core.logging import describe_exception, get_logger
from patchlens.services.embedding.base import EmbeddingProvider
from patchlens.services.llm.base import LLMProvider
from patchlens.services.providers.factory import build_embedding_provider, build_llm_provider
from patchlens.services.review.checks import catalogue

logger = get_logger(__name__)

router = APIRouter(tags=["system"])


@router.get("/healthz", response_model=HealthResponse, summary="Liveness")
def liveness(settings: Settings = Depends(settings_dependency)) -> HealthResponse:
    """Process liveness. Does not touch the database."""
    return HealthResponse(
        status="ok",
        version=__version__,
        environment=settings.environment,
        database="not_checked",
    )


@router.get("/readyz", response_model=HealthDetailResponse, summary="Readiness")
def readiness(
    response: Response,
    session: Session = Depends(read_session),
    settings: Settings = Depends(settings_dependency),
) -> HealthDetailResponse:
    """Readiness: database reachable, providers resolvable, worker observability."""
    checks: dict[str, object] = {}

    try:
        session.execute(text("SELECT 1"))
        checks["database"] = {"ok": True, "dialect": session.get_bind().dialect.name}
    except Exception as exc:
        detail = describe_exception(exc)
        checks["database"] = {"ok": False, "error": detail["message"]}

    checks["llm"] = _provider_check(settings.llm_provider, build_llm_provider, settings)
    checks["embedding"] = _provider_check(settings.embedding_provider, build_embedding_provider, settings)
    checks["test_execution"] = _sandbox_status(settings)
    checks["github_publishing"] = {
        "publish_enabled": settings.github_publish_enabled,
        "webhook_secret_configured": settings.github_webhook_secret is not None,
        "reachable": False,
        "detail": "no outbound GitHub API call is made by this endpoint",
    }

    ready = bool(checks["database"]["ok"])  # type: ignore[index]
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthDetailResponse(status="ok" if ready else "degraded", checks=checks)


@router.get("/api/v1/meta", response_model=MetaResponse, summary="Runtime metadata for the UI")
def meta(settings: Settings = Depends(settings_dependency)) -> MetaResponse:
    """Everything the dashboard needs to render honest capability badges."""
    return MetaResponse(
        app_name=settings.app_name,
        version=__version__,
        environment=settings.environment,
        demo_mode=settings.demo_mode_enabled,
        llm=settings.llm_summary(),
        embedding=settings.embedding_summary(),
        retrieval={
            "strategy": settings.retrieval_strategy,
            "top_k": settings.retrieval_top_k,
            "candidate_k": settings.retrieval_candidate_k,
            "rrf_k": settings.retrieval_rrf_k,
            "reranker": settings.reranker,
            "semantic_backend": "pgvector" if settings.uses_pgvector else "portable_scan",
            "pgvector_available": settings.uses_pgvector,
        },
        capabilities={
            "github_app": bool(settings.github_app_id),
            "github_publishing": settings.github_publish_enabled,
            "test_execution": settings.test_execution_mode,
            "local_repository_registration": not settings.is_production,
            "auth_required": settings.api_auth_token is not None,
        },
        review_rules=catalogue(),
        retrieval_strategies=[strategy.value for strategy in RetrievalStrategy],
        feedback_labels=[label.value for label in FeedbackLabel],
    )


def _provider_check(name: str, builder: object, settings: Settings) -> dict[str, object]:
    try:
        provider = builder(settings)  # type: ignore[operator]
    except Exception as exc:
        detail = describe_exception(exc)
        return {"ok": False, "provider": name, "error": detail["message"]}
    if isinstance(provider, LLMProvider | EmbeddingProvider):
        payload: dict[str, object] = {"ok": True, **provider.describe()}
        if getattr(provider, "is_mock", False):
            payload["note"] = "deterministic development provider: no model is called"
        return payload
    return {"ok": True, "provider": name}


def _sandbox_status(settings: Settings) -> dict[str, object]:
    mode = settings.test_execution_mode
    if mode == "disabled":
        return {
            "mode": mode,
            "available": False,
            "detail": "candidate tests are generated but never executed in this deployment",
        }
    if mode == "subprocess":
        return {
            "mode": mode,
            "available": settings.allow_local_test_execution,
            "detail": (
                "subprocess mode runs candidate tests on this host with a timeout; it is NOT a security "
                "boundary and is refused unless ALLOW_LOCAL_TEST_EXECUTION=true"
            ),
        }
    return {
        "mode": mode,
        "available": True,
        "detail": "candidate tests run in a Docker container with no network and resource limits",
    }


__all__ = ["router"]
