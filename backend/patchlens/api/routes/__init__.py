"""HTTP routers."""

from fastapi import APIRouter, Depends

from patchlens.api.deps import require_auth
from patchlens.api.routes import (
    evaluation,
    findings,
    github,
    health,
    jobs,
    overview,
    repositories,
    reviews,
    settings,
    testing,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(overview.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
# The GitHub router contains one deliberately public route (the HMAC-verified
# webhook receiver); its read endpoints declare the auth dependency themselves.
api_router.include_router(github.router, prefix="/api/v1")
api_router.include_router(repositories.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(reviews.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(findings.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(jobs.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(settings.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(evaluation.router, prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(testing.router, prefix="/api/v1")

__all__ = ["api_router"]
