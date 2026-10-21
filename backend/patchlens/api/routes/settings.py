"""Read-only runtime settings endpoint.

Deliberately read-only: the dashboard must not be able to silently change the
provider, execution sandbox or publication switch at runtime. Those are deployment
configuration (environment variables) and changing them requires a restart, which
this endpoint makes explicit.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from patchlens import __version__
from patchlens.api.deps import settings_dependency
from patchlens.api.schemas import SettingsResponse
from patchlens.core.config import Settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse, summary="Effective runtime configuration")
def get_runtime_settings(settings: Settings = Depends(settings_dependency)) -> SettingsResponse:
    return SettingsResponse(
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
            "max_results_per_file": settings.retrieval_max_results_per_file,
            "semantic_backend": "pgvector" if settings.uses_pgvector else "portable_scan",
            "note": (
                "semantic search uses pgvector when DATABASE_URL points at PostgreSQL with the "
                "extension installed; otherwise it scans stored vectors in Python"
            ),
        },
        security={
            "auth_required": settings.api_auth_token is not None,
            "cors_origins": settings.cors_origins,
            "trust_proxy_headers": settings.trust_proxy_headers,
            "max_request_bytes": settings.max_request_bytes,
            "webhook_signature_verification": True,
        },
        limits={
            "index_max_files": settings.index_max_files,
            "index_max_file_bytes": settings.index_max_file_bytes,
            "review_max_findings": settings.review_max_findings,
            "job_timeout_seconds": settings.job_timeout_seconds,
            "llm_timeout_seconds": settings.llm_timeout_seconds,
        },
        test_generation=settings.test_generation_summary(),
        test_execution={
            "mode": settings.test_execution_mode,
            "network_enabled": settings.test_execution_network_enabled,
            "timeout_seconds": settings.test_execution_timeout_seconds,
            "memory_mb": settings.test_execution_memory_mb,
            "detail": (
                "disabled: candidate tests are never executed; subprocess: host execution, "
                "development only and not a security boundary; docker: isolated container"
            ),
        },
        github={
            "app_configured": settings.github_app_id is not None,
            "webhook_secret_configured": settings.github_webhook_secret is not None,
            "publish_enabled": settings.github_publish_enabled,
            "comment_severities": settings.github_comment_severities,
            "max_comments_per_review": settings.github_max_comments_per_review,
            "note": "changing these requires restarting the API and worker",
        },
    )


__all__ = ["router"]
