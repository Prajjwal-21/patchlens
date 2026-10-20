"""Unit tests for the LLM and embedding providers and the provider factories.

The OpenAI-compatible paths are exercised against ``httpx.MockTransport`` so the
retry, fallback and error-mapping behaviour is tested without network access or
credentials. The mock provider tests pin down that the deterministic baseline is
always labelled as such.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from patchlens.core.config import Settings
from patchlens.core.errors import ProviderError, ProviderNotConfiguredError
from patchlens.services.embedding.hashing import HashingEmbeddingProvider
from patchlens.services.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
from patchlens.services.llm.base import LLMRequest
from patchlens.services.llm.mock import MockLLMProvider
from patchlens.services.llm.openai_compatible import OpenAICompatibleLLMProvider
from patchlens.services.providers import http as http_module
from patchlens.services.providers.factory import build_embedding_provider, build_llm_provider

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "path": {"type": "string"}},
            },
        }
    },
}


def make_request() -> LLMRequest:
    return LLMRequest(
        task="review",
        system="You review code.",
        user="Diff:\n<<<PATCHLENS_UNTRUSTED diff>>>\n+import sys\n",
        schema_name="review_response",
        schema=SCHEMA,
    )


def chat_response(content: str, *, usage: dict[str, int] | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
            ],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
    )


def build_provider(handler: Any, **overrides: Any) -> OpenAICompatibleLLMProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleLLMProvider(
        base_url="https://llm.test/v1",
        api_key="test-key",
        model="test-model",
        max_retries=overrides.pop("max_retries", 1),
        client=client,
        **overrides,
    )


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry backoff is real behaviour; sleeping for it in tests is not."""
    monkeypatch.setattr(http_module.time, "sleep", lambda _seconds: None)


class TestMockProvider:
    def test_is_labelled_as_mock(self) -> None:
        completion = MockLLMProvider().complete(make_request())
        assert completion.is_mock is True
        assert completion.provider == "mock"

    def test_answers_are_deterministic(self) -> None:
        provider = MockLLMProvider()
        first = provider.complete(make_request())
        second = provider.complete(make_request())
        assert first.text == second.text
        assert first.parsed == second.parsed

    def test_returns_schema_shaped_findings(self) -> None:
        completion = MockLLMProvider().complete(make_request())
        assert isinstance(completion.parsed, dict)
        assert isinstance(completion.parsed["findings"], list)

    def test_reports_zero_cost_basis_when_prices_are_not_configured(self) -> None:
        completion = MockLLMProvider().complete(make_request())
        assert completion.usage.estimated_cost_usd is None
        assert completion.usage.cost_basis == "unavailable"


class TestStructuredOutputFallback:
    def test_json_schema_is_tried_first_and_used_when_accepted(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen.append(payload)
            return chat_response('{"findings": []}')

        provider = build_provider(handler)
        completion = provider.complete(make_request())
        assert seen[0]["response_format"]["type"] == "json_schema"
        assert completion.parsed == {"findings": []}
        assert completion.is_mock is False

    def test_rejected_json_schema_falls_back_to_json_object(self) -> None:
        modes: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            mode = payload.get("response_format", {}).get("type", "none")
            modes.append(mode)
            if mode == "json_schema":
                return httpx.Response(400, json={"error": {"message": "response_format not supported"}})
            return chat_response('{"findings": [{"title": "One", "path": "a.py"}]}')

        provider = build_provider(handler)
        completion = provider.complete(make_request())
        assert modes == ["json_schema", "json_object"]
        assert completion.parsed["findings"][0]["title"] == "One"

    def test_all_refused_modes_fall_back_to_a_plain_request(self) -> None:
        modes: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            mode = payload.get("response_format", {}).get("type", "none")
            modes.append(mode)
            if mode in {"json_schema", "json_object"}:
                return httpx.Response(400, json={"error": {"message": "response_format unsupported"}})
            return chat_response('{"findings": []}')

        provider = build_provider(handler)
        assert provider.complete(make_request()).parsed == {"findings": []}
        assert modes == ["json_schema", "json_object", "none"]

    def test_rejected_request_surfaces_the_upstream_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "response_format unsupported"}})

        provider = build_provider(handler)
        with pytest.raises(ProviderError) as excinfo:
            provider.complete(make_request())
        assert excinfo.value.details["status"] == 400
        assert "rejected the request" in str(excinfo.value)

    def test_authentication_failure_is_not_treated_as_a_mode_rejection(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})

        provider = build_provider(handler)
        with pytest.raises(ProviderError) as excinfo:
            provider.complete(make_request())
        assert excinfo.value.details["status"] == 401
        assert len(calls) == 1, "401 must fail immediately instead of retrying output modes"


class TestRetriesAndErrors:
    def test_rate_limit_is_retried_and_can_succeed(self) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) == 1:
                return httpx.Response(429, json={"error": {"message": "slow down"}})
            return chat_response('{"findings": []}')

        provider = build_provider(handler, max_retries=2)
        completion = provider.complete(make_request())
        assert len(attempts) == 2
        assert completion.parsed == {"findings": []}

    def test_server_error_is_retried_then_raised(self) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(503, text="upstream unavailable")

        provider = build_provider(handler, max_retries=2)
        with pytest.raises(ProviderError):
            provider.complete(make_request())
        assert len(attempts) == 3, "one initial attempt plus two retries"

    def test_timeout_becomes_a_provider_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        provider = build_provider(handler)
        with pytest.raises(ProviderError, match="timed out"):
            provider.complete(make_request())

    def test_non_json_body_becomes_a_provider_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        provider = build_provider(handler)
        with pytest.raises(ProviderError, match="non-JSON"):
            provider.complete(make_request())

    def test_prose_response_that_is_not_json_is_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return chat_response("I reviewed the diff and everything looks fine to me.")

        provider = build_provider(handler)
        with pytest.raises(ProviderError, match="not valid JSON"):
            provider.complete(make_request())

    def test_fenced_json_is_accepted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return chat_response('```json\n{"findings": []}\n```')

        provider = build_provider(handler)
        assert provider.complete(make_request()).parsed == {"findings": []}

    def test_json_embedded_in_prose_is_recovered(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return chat_response('Here is the review: {"findings": []} — done.')

        provider = build_provider(handler)
        assert provider.complete(make_request()).parsed == {"findings": []}

    def test_empty_content_is_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return chat_response("   ")

        provider = build_provider(handler)
        with pytest.raises(ProviderError, match="empty content"):
            provider.complete(make_request())

    def test_missing_api_key_refuses_construction(self) -> None:
        with pytest.raises(ProviderError, match="API_KEY"):
            OpenAICompatibleLLMProvider(base_url="https://llm.test/v1", api_key=None, model="m")


class TestUsageAccounting:
    def test_token_usage_is_recorded(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return chat_response('{"findings": []}', usage={"prompt_tokens": 1000, "completion_tokens": 500})

        provider = build_provider(handler)
        usage = provider.complete(make_request()).usage
        assert usage.prompt_tokens == 1000
        assert usage.completion_tokens == 500
        assert usage.total_tokens == 1500
        assert usage.estimated_cost_usd is None, "no price is configured, so no cost may be claimed"

    def test_cost_is_reported_only_when_prices_are_configured(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return chat_response(
                '{"findings": []}', usage={"prompt_tokens": 1_000_000, "completion_tokens": 0}
            )

        provider = build_provider(handler, cost_per_mtok_input_usd=2.0, cost_per_mtok_output_usd=8.0)
        usage = provider.complete(make_request()).usage
        assert usage.estimated_cost_usd == pytest.approx(2.0)
        assert usage.cost_basis == "configured_price_per_mtok"


class TestOpenAICompatibleEmbeddings:
    def build(self, handler: Any, **overrides: Any) -> OpenAICompatibleEmbeddingProvider:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return OpenAICompatibleEmbeddingProvider(
            base_url="https://embed.test/v1",
            api_key="test-key",
            model="text-embedding-test",
            dim=overrides.pop("dim", 4),
            client=client,
            **overrides,
        )

    @staticmethod
    def embeddings_response(vectors: list[list[float]], *, shuffled: bool = False) -> httpx.Response:
        data = [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ]
        if shuffled:
            data.reverse()
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": data,
                "model": "text-embedding-test",
                "usage": {"total_tokens": 7},
            },
        )

    def test_requires_an_api_key(self) -> None:
        with pytest.raises(ProviderError, match="EMBEDDING_API_KEY"):
            OpenAICompatibleEmbeddingProvider(
                base_url="https://embed.test/v1", api_key=None, model="m", dim=4
            )

    def test_documents_are_embedded_in_one_request_and_marked_neural(self) -> None:
        bodies: list[Any] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return self.embeddings_response([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])

        batch = self.build(handler).embed_documents(["first", "second"])
        assert len(bodies) == 1
        assert bodies[0] == {"model": "text-embedding-test", "input": ["first", "second"]}
        assert batch.is_neural is True
        assert batch.provider == "openai_compatible"
        assert batch.dim == 4
        assert batch.token_estimate == 7
        assert batch.vectors == [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]

    def test_responses_are_reordered_by_index(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return self.embeddings_response([[1.0, 0.0, 0.0, 0.0], [0.0, 2.0, 0.0, 0.0]], shuffled=True)

        batch = self.build(handler).embed_documents(["first", "second"])
        assert batch.vectors[0][0] == 1.0
        assert batch.vectors[1][1] == 2.0

    def test_large_inputs_are_split_into_batches(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            calls.append(len(payload["input"]))
            return self.embeddings_response([[0.1, 0.2, 0.3, 0.4]] * len(payload["input"]))

        provider = self.build(handler, batch_size=2)
        batch = provider.embed_documents(["a", "b", "c"])
        assert calls == [2, 1]
        assert len(batch.vectors) == 3

    def test_blank_inputs_are_replaced_with_a_space(self) -> None:
        seen: list[list[str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen.append(payload["input"])
            return self.embeddings_response([[0.0, 0.0, 0.0, 1.0]])

        self.build(handler).embed_documents(["   "])
        assert seen == [[" "]], "providers reject empty strings"

    def test_count_mismatch_is_a_provider_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return self.embeddings_response([[1.0, 0.0, 0.0, 0.0]])

        with pytest.raises(ProviderError, match="unexpected payload"):
            self.build(handler).embed_documents(["one", "two"])

    def test_dimension_mismatch_is_refused_rather_than_stored(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return self.embeddings_response([[1.0, 0.0, 0.0]])

        with pytest.raises(ProviderError, match="dimension mismatch"):
            self.build(handler, dim=4).embed_documents(["one"])

    def test_empty_vector_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return self.embeddings_response([[]])

        with pytest.raises(ProviderError, match="empty vector"):
            self.build(handler).embed_documents(["one"])

    def test_query_embedding_goes_through_the_same_validated_path(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return self.embeddings_response([[0.5, 0.5, 0.0, 0.0]])

        assert self.build(handler).embed_query("find me") == [0.5, 0.5, 0.0, 0.0]

    def test_describe_reports_neural_opt_in(self) -> None:
        described = self.build(lambda request: self.embeddings_response([])).describe()
        assert described == {
            "provider": "openai_compatible",
            "model": "text-embedding-test",
            "dim": 4,
            "is_neural": True,
        }


class TestFactories:
    def test_mock_and_hashing_are_the_documented_defaults(self) -> None:
        settings = Settings(environment="test")
        assert settings.llm_provider == "mock"
        assert settings.embedding_provider == "hashing"
        assert build_llm_provider(settings).is_mock is True
        assert build_embedding_provider(settings).is_neural is False

    def test_openai_llm_without_a_key_is_refused(self) -> None:
        settings = Settings(environment="test", llm_provider="openai_compatible", llm_api_key=None)
        with pytest.raises(ProviderNotConfiguredError, match="LLM_API_KEY"):
            build_llm_provider(settings)

    def test_openai_embedding_without_a_key_is_refused(self) -> None:
        settings = Settings(
            environment="test", embedding_provider="openai_compatible", embedding_api_key=None
        )
        with pytest.raises(ProviderNotConfiguredError, match="EMBEDDING_API_KEY"):
            build_embedding_provider(settings)

    def test_unknown_provider_cannot_even_be_configured(self) -> None:
        # The provider name is a validated literal, so a typo fails at config time
        # with a readable message instead of at the first review.
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="llm_provider"):
            Settings(environment="test", llm_provider="gpt-from-a-dream")


class TestHashingEmbeddings:
    def test_vectors_are_deterministic_and_normalised(self) -> None:
        provider = HashingEmbeddingProvider(dim=128)
        first = provider.embed_query("def add(a, b): return a + b")
        second = provider.embed_query("def add(a, b): return a + b")
        assert first == second
        assert len(first) == 128
        norm = sum(value * value for value in first) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)
        assert provider.is_neural is False

    def test_similar_text_scores_higher_than_unrelated_text(self) -> None:
        provider = HashingEmbeddingProvider(dim=512)

        def cosine(left: list[float], right: list[float]) -> float:
            return sum(a * b for a, b in zip(left, right, strict=True))

        query = provider.embed_query("verify the github webhook signature")
        close = provider.embed_query("def verify_github_signature(): compare hmac digest of the webhook")
        far = provider.embed_query("render the settings page with tailwind classes")
        assert cosine(query, close) > cosine(query, far)

    def test_batch_reports_the_model_and_neural_flag(self) -> None:
        batch = HashingEmbeddingProvider(dim=64).embed_documents(["a", "b"])
        assert len(batch.vectors) == 2
        assert batch.model == "hashing-ngram-v2"
        assert batch.is_neural is False

    def test_empty_input_returns_no_vectors(self) -> None:
        assert HashingEmbeddingProvider(dim=64).embed_documents([]).vectors == []

    def test_dimension_is_honoured(self) -> None:
        with pytest.raises(ValueError):
            HashingEmbeddingProvider(dim=0)
