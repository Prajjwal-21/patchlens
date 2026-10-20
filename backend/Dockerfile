# PatchLens API/worker image.
#
# One image serves both processes: the command decides whether the container is
# the HTTP API or a background worker. Migrations are a one-shot container in
# docker-compose.yml, so neither long-running process races to change the schema.

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libpq is only needed at build time by the psycopg binary wheel; the runtime
# needs no compiler or database client.
RUN groupadd --system --gid 10001 patchlens \
    && useradd --system --uid 10001 --gid patchlens --create-home patchlens

WORKDIR /app

COPY requirements.lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements.lock.txt

COPY patchlens ./patchlens
COPY migrations ./migrations
COPY alembic.ini pyproject.toml ./

# A writable directory for SQLite fallback data and for checkouts mounted by an
# operator. The image itself runs as a non-root user.
RUN mkdir -p /app/var && chown -R patchlens:patchlens /app

USER patchlens
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["python", "-m", "patchlens.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
