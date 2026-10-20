"""HTTP routers."""

from fastapi import APIRouter, Depends

from patchlens.api.deps import require_auth
from patchlens.api.routes import (
    evaluation,
    findings,
    health,
    jobs,
    repositories,
    reviews,
    settings,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(repositories.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(reviews.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(findings.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(jobs.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(settings.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(evaluation.router, prefix="/api/v1", dependencies=[Depends(require_auth)])

__all__ = ["api_router"]
