"""OpenAI-compatible chat-completions provider.

Works with the OpenAI API and any compatible gateway (Azure-style paths, Groq,
Together, vLLM, Ollama's compatibility endpoint, OpenRouter, ...). Only the four
documented settings are required: ``LLM_BASE_URL``, ``LLM_API_KEY``,
``LLM_MODEL`` and ``LLM_PROVIDER``.

Structured output strategy, in order:

1. ask for ``response_format: {"type": "json_schema", ...}`` (strict);
2. if the endpoint rejects that, retry once with ``{"type": "json_object"}``;
3. parse defensively (strip code fences, locate the first JSON object).

Whatever comes back is still validated by the review engine: the provider never
gets to write to the database unchecked.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from patchlens.core.errors import ProviderError
from patchlens.core.logging import describe_exception, get_logger, log_extra
from patchlens.services.llm.base import LLMCompletion, LLMProvider, LLMRequest, LLMUsage
from patchlens.services.providers.http import HttpClient

logger = get_logger(__name__)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

#: Statuses some gateways use to refuse ``response_format`` outright. They are
#: distinct from 401/403 (bad credentials) and 429/5xx (retryable), which must
#: never be swallowed by the structured-output fallback.
MODE_REJECTION_STATUS: frozenset[int] = frozenset({400, 404, 415, 422})

STRUCTURED_OUTPUT_HINT = (
    "Respond with a single JSON object that conforms to the requested schema. "
    "Do not wrap it in prose or code fences."
)


class OpenAICompatibleLLMProvider(LLMProvider):
    """Chat-completions client with strict-ish structured output."""

    name = "openai_compatible"
    is_mock = False

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 45.0,
        max_retries: int = 2,
        max_output_tokens: int = 2048,
        temperature: float = 0.0,
        cost_per_mtok_input_usd: float = 0.0,
        cost_per_mtok_output_usd: float = 0.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError(
                "LLM provider 'openai_compatible' requires LLM_API_KEY",
                details={"provider": self.name},
            )
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.cost_in = cost_per_mtok_input_usd
        self.cost_out = cost_per_mtok_output_usd
        self._http = HttpClient(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            client=client,
        )

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ public
    def complete(self, request: LLMRequest) -> LLMCompletion:
        started = time.perf_counter()
        messages = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": min(request.max_output_tokens, self.max_output_tokens),
        }
        if request.schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": False,
                    "schema": request.schema,
                },
            }

        response = self._post(payload)
        if response is None:
            # Endpoint refused json_schema; retry with the softer json_object mode.
            payload["response_format"] = {"type": "json_object"}
            response = self._post(payload)
        if response is None:
            payload.pop("response_format", None)
            response = self._post(payload)
        if response is None:
            raise ProviderError("chat completion failed for every structured-output mode")

        text, finish_reason, usage = self._extract(response)
        parsed = _parse_json_object(text) if request.schema else None
        if request.schema and parsed is None:
            raise ProviderError(
                "model response was not valid JSON",
                details={"provider": self.name, "model": self.model, "preview": text[:200]},
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "llm completion",
            extra=log_extra(
                provider=self.name,
                model=self.model,
                task=request.task,
                latency_ms=latency_ms,
                total_tokens=usage.total_tokens,
            ),
        )
        return LLMCompletion(
            text=text,
            provider=self.name,
            model=self.model,
            is_mock=False,
            parsed=parsed,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )

    # ----------------------------------------------------------------- internals
    def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST one structured-output mode; return ``None`` when the mode itself was refused.

        Endpoints disagree about ``response_format``: OpenAI and most gateways accept
        ``json_schema``, others answer 400/404/415/422 for it, and a few reject
        ``json_object`` as well. Only those mode-level refusals trigger the next mode;
        authentication, rate limiting and server failures are raised to the caller.
        """
        try:
            return self._http.post_json("/chat/completions", payload, provider="llm")
        except ProviderError as exc:
            mode = (payload.get("response_format") or {}).get("type")
            upstream_status = exc.details.get("status")
            if mode and upstream_status in MODE_REJECTION_STATUS:
                logger.info(
                    "endpoint rejected structured-output mode; trying a looser one",
                    extra=log_extra(mode=mode, status=upstream_status),
                )
                return None
            raise

    def _extract(self, response: dict[str, Any]) -> tuple[str, str | None, LLMUsage]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("chat completion response contained no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise ProviderError("chat completion choice malformed")
        message = first.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # some gateways return content parts
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("chat completion returned empty content")
        finish_reason = first.get("finish_reason")

        usage_block = response.get("usage") or {}
        prompt_tokens = int(usage_block.get("prompt_tokens") or 0)
        completion_tokens = int(usage_block.get("completion_tokens") or 0)
        total = int(usage_block.get("total_tokens") or (prompt_tokens + completion_tokens))
        usage = LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            requests=1,
            **self._cost(prompt_tokens, completion_tokens),
        )
        return content, finish_reason if isinstance(finish_reason, str) else None, usage

    def _cost(self, prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
        if self.cost_in <= 0 and self.cost_out <= 0:
            return {"estimated_cost_usd": None, "cost_basis": "unavailable"}
        cost = (prompt_tokens / 1_000_000) * self.cost_in + (completion_tokens / 1_000_000) * self.cost_out
        return {
            "estimated_cost_usd": round(cost, 6),
            "cost_basis": "configured_price_per_mtok",
        }


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort JSON object extraction from a model response."""
    candidate = _FENCE_RE.sub("", text).strip()
    for attempt in (candidate, _first_balanced_object(candidate)):
        if not attempt:
            continue
        try:
            parsed = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"findings": parsed}
    logger.warning("could not parse model output as JSON", extra=log_extra(preview=text[:120]))
    return None


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


__all__ = ["OpenAICompatibleLLMProvider", "describe_exception"]
