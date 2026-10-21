# Minimal test-runner image for PatchLens' Docker execution tier.
#
# The API/worker image does not contain pytest and is not intended to execute
# generated tests. Build this image once:
#
#   docker build -t patchlens-test-runner:latest docker/test-runner
#
# PatchLens runs it with --network none, read-only mounts, dropped capabilities,
# a read-only root filesystem, resource limits and an unprivileged user. That is
# defense in depth, not a guarantee that arbitrary generated code is safe; review
# the sandbox limitations in docs/SECURITY.md before enabling Docker execution.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m pip install --no-cache-dir "pytest==9.1.1"

RUN groupadd --system --gid 10001 patchlens \
    && useradd --system --uid 10001 --gid patchlens --create-home patchlens

WORKDIR /tests
USER patchlens

CMD ["python", "-m", "pytest", "--version"]
