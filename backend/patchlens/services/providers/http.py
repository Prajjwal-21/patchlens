"""HTTP client shared by the OpenAI-compatible LLM and embedding providers."""

from __future__ import annotations

import time
from typing import Any

import httpx

from patchlens.core.errors import ProviderError, RateLimitedError
from patchlens.core.logging import describe_exception, get_logger, log_extra

logger = get_logger(__name__)

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524}


class HttpClient:
    """Small bounded-retry JSON client.

    Retries only idempotent failures (transport errors, 429 and 5xx) with
    exponential backoff and full jitter. Non-retryable 4xx responses surface
    immediately with a redacted message so a misconfiguration is obvious.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float,
        max_retries: int = 2,
        headers: dict[str, str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def post_json(self, path: str, payload: dict[str, Any], *, provider: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post(url, json=payload, headers=self._headers)
            except httpx.TimeoutException as exc:
                last_error = ProviderError(f"{provider} request timed out", details={"url": url})
                logger.warning(
                    "provider timeout",
                    extra=log_extra(provider=provider, attempt=attempt, **describe_exception(exc)),
                )
            except httpx.HTTPError as exc:
                last_error = ProviderError(f"{provider} transport error", details={"url": url})
                logger.warning(
                    "provider transport error", extra=log_extra(provider=provider, **describe_exception(exc))
                )
            else:
                if response.status_code < 400:
                    try:
                        body: dict[str, Any] = response.json()
                        return body
                    except ValueError as exc:
                        raise ProviderError(
                            f"{provider} returned a non-JSON response",
                            details={"status": response.status_code},
                        ) from exc

                body_preview = response.text[:500]
                if response.status_code in RETRYABLE_STATUS:
                    last_error = ProviderError(
                        f"{provider} returned retryable status {response.status_code}",
                        details={"status": response.status_code},
                    )
                    if response.status_code == 429:
                        last_error = RateLimitedError(
                            f"{provider} rate limited the request", details={"status": 429}
                        )
                else:
                    raise ProviderError(
                        f"{provider} rejected the request with status {response.status_code}",
                        details={"status": response.status_code, "body": body_preview},
                    )
                logger.warning(
                    "provider retryable failure",
                    extra=log_extra(provider=provider, status=response.status_code, attempt=attempt),
                )

            if attempt < self.max_retries:
                time.sleep(min(8.0, 0.5 * (2**attempt)) * (0.5 + _jitter()))

        raise last_error or ProviderError(f"{provider} request failed")


def _jitter() -> float:
    import random

    return random.random()  # noqa: S311 - jitter, not a security decision


__all__ = ["RETRYABLE_STATUS", "HttpClient"]
