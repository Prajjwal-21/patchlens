"""GitHub App client tests.

These tests use a real RS256 key generated for the test process and
``httpx.MockTransport``; no network is involved and no real credential is used.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from patchlens.core.config import Settings
from patchlens.core.errors import ProviderError, ProviderNotConfiguredError
from patchlens.services.github.client import GitHubClient, build_github_client

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_KEY_PEM = _KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
PUBLIC_KEY_PEM = (
    _KEY.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode()
)


def client_with(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    clock: Callable[[], datetime] | None = None,
) -> GitHubClient:
    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.test",
    )
    return GitHubClient(
        github_settings_from_env(),
        http=http,
        clock=clock,
    )


def github_settings_from_env() -> Settings:
    """A Settings object without needing the app/database fixtures."""
    return Settings(
        environment="test",
        github_app_id=12345,
        github_private_key=SecretStr(PRIVATE_KEY_PEM),
        github_webhook_secret=SecretStr("test-webhook-secret"),
    )


class TestAppAuthentication:
    def test_app_jwt_is_short_lived_and_signed_by_the_configured_key(self) -> None:
        client = client_with(lambda request: httpx.Response(200))
        token = client.app_jwt()
        claims = jwt.decode(
            token,
            PUBLIC_KEY_PEM,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert claims["iss"] == "12345"
        assert 500 <= claims["exp"] - claims["iat"] <= 600
        client.close()

    def test_missing_credentials_are_a_configuration_error_not_a_fake_token(self) -> None:
        settings = Settings(environment="test", github_app_id=12345)
        with pytest.raises(ProviderNotConfiguredError):
            GitHubClient(settings).app_jwt()

    def test_private_key_with_escaped_newlines_is_accepted(self) -> None:
        settings = github_settings_from_env().model_copy(
            update={"github_private_key": SecretStr(PRIVATE_KEY_PEM.replace("\n", "\\n"))}
        )
        with GitHubClient(settings) as client:
            token = client.app_jwt()
        claims = jwt.decode(
            token,
            PUBLIC_KEY_PEM,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert claims["iss"] == "12345"

    def test_installation_tokens_are_cached_and_refreshed_before_expiry(self) -> None:
        calls: list[str] = []
        now = [datetime(2026, 10, 20, 12, 0, tzinfo=UTC)]

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(
                201,
                json={"token": "ghs_test_token", "expires_at": "2026-10-20T13:00:00Z"},
            )

        client = client_with(handler, clock=lambda: now[0])
        assert client.installation_token(42) == "ghs_test_token"
        assert client.installation_token(42) == "ghs_test_token"
        assert calls == ["/app/installations/42/access_tokens"]

        now[0] = now[0] + timedelta(hours=2)
        assert client.installation_token(42) == "ghs_test_token"
        assert len(calls) == 2
        client.close()


class TestApiCalls:
    def test_diff_request_asks_for_the_diff_media_type(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/app/installations/"):
                return httpx.Response(
                    201,
                    json={"token": "ghs_test_token", "expires_at": "2026-10-20T13:00:00Z"},
                )
            seen.append(request)
            return httpx.Response(200, text="diff --git a/x.py b/x.py\n+pass\n")

        client = client_with(handler)
        diff = client.pull_request_diff(repository="acme/widgets", number=7, installation_id=42)
        assert diff.startswith("diff --git")
        assert seen[0].headers["accept"] == "application/vnd.github.v3.diff"
        assert seen[0].headers["authorization"] == "Bearer ghs_test_token"
        client.close()

    def test_review_is_posted_as_a_comment_never_an_approval(self) -> None:
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/app/installations/"):
                return httpx.Response(
                    201,
                    json={"token": "ghs_test_token", "expires_at": "2026-10-20T13:00:00Z"},
                )
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"html_url": "https://github.test/review/1"})

        client = client_with(handler)
        result = client.create_review(
            repository="acme/widgets",
            number=7,
            installation_id=42,
            commit_id="abc123",
            body="summary",
            comments=[{"path": "x.py", "line": 4, "side": "RIGHT", "body": "finding"}],
        )
        assert result["html_url"] == "https://github.test/review/1"
        assert captured[0]["event"] == "COMMENT"
        assert captured[0]["commit_id"] == "abc123"
        assert captured[0]["comments"][0]["side"] == "RIGHT"
        client.close()

    def test_api_errors_are_bounded_and_never_include_the_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/app/installations/"):
                return httpx.Response(
                    201,
                    json={"token": "ghs_super_secret", "expires_at": "2026-10-20T13:00:00Z"},
                )
            return httpx.Response(
                422,
                json={"message": "Validation Failed", "errors": [{"code": "custom"}]},
            )

        client = client_with(handler)
        with pytest.raises(ProviderError) as caught:
            client.create_review(
                repository="acme/widgets",
                number=7,
                installation_id=42,
                commit_id="abc",
                body="body",
                comments=[],
            )
        message = str(caught.value)
        assert "422" in message
        assert "Validation Failed" in message
        assert "ghs_super_secret" not in message
        assert caught.value.details["status"] == 422
        client.close()

    def test_unconfigured_builder_does_not_create_a_client(self) -> None:
        with pytest.raises(ProviderNotConfiguredError):
            build_github_client(Settings(environment="test"))
