"""Minimal GitHub App client used by the webhook worker and publisher.

Only the endpoints PatchLens needs are implemented, and every request is made with
an installation token scoped to the repository being reviewed. App JWTs are signed
with the configured private key and never logged; installation tokens are cached in
memory only and are never persisted to the database.

The HTTP client is injectable so tests can use ``httpx.MockTransport`` without
reaching the network. No method invents a successful response: non-2xx responses
raise :class:`ProviderError` with the status and a bounded, redacted message.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

from patchlens.core.config import Settings
from patchlens.core.errors import ProviderError, ProviderNotConfiguredError
from patchlens.core.logging import get_logger, log_extra, redact

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
#: GitHub rejects app JWTs that live longer than 10 minutes.
JWT_LIFETIME = timedelta(minutes=9)
JWT_BACKDATE = timedelta(seconds=60)
#: Refresh installation tokens a minute before they expire.
TOKEN_REFRESH_MARGIN = timedelta(seconds=60)
MAX_DIFF_BYTES = 5 * 1024 * 1024


class GitHubClient:
    """Installation-scoped GitHub REST client."""

    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tokens: dict[str, tuple[str, datetime]] = {}
        if http is not None:
            self._http = http
        else:
            self._http = httpx.Client(
                base_url=settings.github_api_base_url.rstrip("/"),
                timeout=httpx.Timeout(DEFAULT_TIMEOUT_SECONDS),
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "PatchLens",
                },
            )

    # --------------------------------------------------------------- lifecycle
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------- auth
    def app_jwt(self) -> str:
        """Create a short-lived RS256 app JWT.

        The app id is the issuer; GitHub accepts ``iat`` slightly in the past to
        tolerate clock skew. Failure to parse the key is a configuration error, not
        a transport error, and never includes the key material in the message.
        """
        if self.settings.github_app_id is None or self.settings.github_private_key is None:
            raise ProviderNotConfiguredError(
                "GitHub App authentication requires GITHUB_APP_ID and GITHUB_PRIVATE_KEY"
            )
        now = self._clock()
        payload = {
            "iat": int((now - JWT_BACKDATE).timestamp()),
            "exp": int((now + JWT_LIFETIME).timestamp()),
            "iss": str(self.settings.github_app_id),
        }
        key = self.settings.github_private_key.get_secret_value()
        # Deployment systems often store PEM keys with literal ``\n`` escapes in a
        # single environment variable. Accept both forms; the key material itself is
        # never logged either way.
        if "\\n" in key and "\n" not in key:
            key = key.replace("\\n", "\n")
        try:
            token = jwt.encode(payload, key, algorithm="RS256")
        except Exception as exc:  # PyJWT/cryptography raise several key-format errors
            raise ProviderNotConfiguredError(
                "GitHub App private key could not sign a JWT; check GITHUB_PRIVATE_KEY"
            ) from exc
        return str(token)

    def installation_token(self, installation_id: str | int) -> str:
        """Return a cached installation token, fetching one when needed."""
        key = str(installation_id)
        cached = self._tokens.get(key)
        now = self._clock()
        if cached is not None and cached[1] - TOKEN_REFRESH_MARGIN > now:
            return cached[0]

        response = self._request(
            "POST",
            f"/app/installations/{key}/access_tokens",
            token=self.app_jwt(),
            auth_scope="app",
        )
        try:
            body = response.json()
            token = str(body["token"])
            expires_at = _parse_github_timestamp(body.get("expires_at"))
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("GitHub returned an unreadable installation token response") from exc
        self._tokens[key] = (token, expires_at)
        logger.info(
            "github installation token acquired",
            extra=log_extra(installation_id=key, expires_at=expires_at.isoformat()),
        )
        return token

    # ------------------------------------------------------------------- calls
    def pull_request(self, *, repository: str, number: int, installation_id: str | int) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/repos/{repository}/pulls/{number}",
            token=self.installation_token(installation_id),
        )
        return _json_object(response, "pull request")

    def pull_request_diff(self, *, repository: str, number: int, installation_id: str | int) -> str:
        response = self._request(
            "GET",
            f"/repos/{repository}/pulls/{number}",
            token=self.installation_token(installation_id),
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        content = response.content
        if len(content) > MAX_DIFF_BYTES:
            raise ProviderError(
                f"pull request diff is larger than the {MAX_DIFF_BYTES}-byte limit",
                details={"bytes": len(content)},
            )
        return content.decode("utf-8", errors="replace")

    def create_review(
        self,
        *,
        repository: str,
        number: int,
        installation_id: str | int,
        commit_id: str,
        body: str,
        comments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/repos/{repository}/pulls/{number}/reviews",
            token=self.installation_token(installation_id),
            json={
                "commit_id": commit_id,
                "body": body,
                # COMMENT never approves or requests changes: PatchLens reports,
                # a human decides.
                "event": "COMMENT",
                "comments": comments,
            },
        )
        return _json_object(response, "review publication")

    def create_check_run(
        self,
        *,
        repository: str,
        installation_id: str | int,
        name: str,
        head_sha: str,
        conclusion: str,
        title: str,
        summary: str,
        annotations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/repos/{repository}/check-runs",
            token=self.installation_token(installation_id),
            json={
                "name": name,
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": conclusion,
                "output": {
                    "title": title[:255],
                    "summary": summary[:65_535],
                    "annotations": annotations[:50],
                },
            },
        )
        return _json_object(response, "check run")

    # --------------------------------------------------------------- internals
    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        auth_scope: str = "",
        **kwargs: Any,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        extra_headers = kwargs.pop("headers", None)
        if extra_headers:
            headers.update(extra_headers)
        try:
            response = self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"GitHub API request failed: {type(exc).__name__}",
                details={"path": path},
            ) from exc
        if response.status_code >= 400:
            message = _error_message(response)
            logger.warning(
                "github api error",
                extra=log_extra(
                    method=method,
                    path=path,
                    status=response.status_code,
                    auth_scope=auth_scope,
                ),
            )
            raise ProviderError(
                f"GitHub API {method} {path} returned HTTP {response.status_code}: {message}",
                details={"status": response.status_code},
            )
        return response


def _parse_github_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("missing expires_at")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_object(response: httpx.Response, what: str) -> dict[str, Any]:
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderError(f"GitHub returned non-JSON data for {what}") from exc
    if not isinstance(body, dict):
        raise ProviderError(f"GitHub returned an unexpected payload for {what}")
    return body


def _error_message(response: httpx.Response) -> str:
    """Bounded error detail without echoing request headers or tokens."""
    try:
        body = response.json()
        message = body.get("message") if isinstance(body, dict) else None
        reasons = body.get("errors") if isinstance(body, dict) else None
        detail = str(message or "no message")
        if reasons:
            detail += f" ({redact(reasons)})"
    except (json.JSONDecodeError, ValueError):
        detail = redact(response.text)
    return detail[:500]


def build_github_client(settings: Settings, *, http: httpx.Client | None = None) -> GitHubClient:
    """Create a client, refusing to pretend credentials exist when they do not."""
    if not settings.github_configured:
        raise ProviderNotConfiguredError(
            "GitHub is not configured: set GITHUB_APP_ID, GITHUB_PRIVATE_KEY and GITHUB_WEBHOOK_SECRET"
        )
    return GitHubClient(settings, http=http)


__all__ = [
    "GitHubClient",
    "build_github_client",
]
