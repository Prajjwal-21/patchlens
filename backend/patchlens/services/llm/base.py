"""LLM provider interface.

The provider used by the running PatchLens deployment is decided purely by
configuration (``LLM_PROVIDER`` / ``LLM_BASE_URL`` / ``LLM_API_KEY`` /
``LLM_MODEL``). The model that happens to be driving a coding agent is irrelevant
and is never assumed to be reachable from this application.

Contracts every provider must honour:

* ``is_mock`` is ``True`` for providers that do not call a real model, and that
  flag is persisted with every review so output can never be mistaken for
  model-generated analysis;
* responses are returned as structured payloads that the caller validates with
  Pydantic - a provider never gets to bypass schema validation;
* failures raise :class:`~patchlens.core.errors.ProviderError` (retryable) so the
  job queue can back off instead of losing work.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

TASK_REVIEW_FINDINGS = "review_findings"
TASK_GENERATE_TEST = "generate_test"


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """Token accounting; costs are only reported when prices are configured."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    requests: int = 1
    estimated_cost_usd: float | None = None
    cost_basis: str = "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "requests": self.requests,
            "estimated_cost_usd": self.estimated_cost_usd,
            "cost_basis": self.cost_basis,
        }


@dataclass(slots=True)
class LLMRequest:
    """A structured request: rendered prompts plus the machine-readable payload.

    ``payload`` is what deterministic providers consume; ``system``/``user`` are
    what HTTP providers send. Both describe the same task, and both are built from
    fenced, untrusted-input-safe context.
    """

    task: str
    system: str
    user: str
    schema_name: str
    schema: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    max_output_tokens: int = 2048
    temperature: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMCompletion:
    """Provider response: raw text plus best-effort parsed structure."""

    text: str
    provider: str
    model: str
    is_mock: bool
    parsed: dict[str, Any] | None = None
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: int = 0
    finish_reason: str | None = None
    http_attempts: int = 1
    raw_meta: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Base class for review/test-generation backends."""

    name: str = "unknown"
    model: str = "unknown"
    is_mock: bool = False

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMCompletion:
        """Execute ``request`` and return a completion."""

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model, "is_mock": self.is_mock}

    def close(self) -> None:  # noqa: B027 - optional hook; stateless providers hold no resources
        """Release transport resources (no-op for stateless providers)."""


__all__ = [
    "TASK_GENERATE_TEST",
    "TASK_REVIEW_FINDINGS",
    "LLMCompletion",
    "LLMProvider",
    "LLMRequest",
    "LLMUsage",
]
