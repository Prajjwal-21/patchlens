"""Deterministic, dependency-free local embedder.

**This is not a neural model and must never be described as one.**

It is a signed feature-hashing bag-of-words encoder:

* identifiers are split on ``snake_case`` / ``camelCase`` / punctuation;
* unigrams and bigrams are hashed into ``dim`` buckets with a signed hash
  (the sign reduces collision bias);
* term frequencies are damped sub-linearly (``1 + log(tf)``);
* vectors are L2-normalised so cosine similarity is a dot product.

Why it exists: PatchLens must run, be tested, and be demonstrable with zero
credentials and zero downloads. It gives real similarity signal for identifier
and terminology overlap and is a legitimate *baseline* in the retrieval
evaluation - but for genuine semantic retrieval you configure
``EMBEDDING_PROVIDER=openai_compatible``.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from itertools import pairwise

from patchlens.services.embedding.base import EmbeddingBatch, EmbeddingProvider

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,63}|[0-9]{2,}")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "not",
        "are",
        "was",
        "were",
        "has",
        "have",
        "def",
        "class",
        "return",
        "self",
        "cls",
        "import",
        "none",
        "true",
        "false",
        "value",
        "name",
        "type",
        "object",
        "str",
        "int",
        "bool",
        "float",
        "list",
        "dict",
        "set",
        "tuple",
    }
)


def tokenise(text: str) -> list[str]:
    """Split text into normalised identifier tokens.

    Snake_case identifiers contribute both the full token (``safe_divide``) and
    their parts (``safe``, ``divide``), so a query mentioning the concept still
    matches the compound name and vice versa. CamelCase is split the same way.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        for part in _CAMEL_RE.sub(" ", raw).split():
            lowered = part.lower()
            for candidate in _identifier_parts(lowered):
                if len(candidate) < 2 or candidate in _STOPWORDS:
                    continue
                tokens.append(candidate)
    return tokens


def _identifier_parts(token: str) -> Iterator[str]:
    """Yield the identifier and, for snake_case, each of its parts."""
    yield token
    if "_" in token:
        yield from (part for part in token.split("_") if part)


def _bucket(token: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % dim
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return index, sign


class HashingEmbeddingProvider(EmbeddingProvider):
    """Signed feature hashing over word unigrams and bigrams."""

    name = "hashing"
    is_neural = False

    def __init__(self, *, dim: int = 512, model: str = "hashing-ngram-v2") -> None:
        super().__init__(dim=dim)
        self.model = model

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatch:
        vectors: list[list[float]] = []
        token_total = 0
        for text in texts:
            vector, tokens = self._encode(text)
            vectors.append(vector)
            token_total += tokens
        return EmbeddingBatch(
            vectors=vectors,
            provider=self.name,
            model=self.model,
            dim=self.dim,
            is_neural=False,
            token_estimate=token_total,
        )

    # ------------------------------------------------------------------ internals
    def _encode(self, text: str) -> tuple[list[float], int]:
        tokens = tokenise(text)
        counts: Counter[str] = Counter(tokens)
        counts.update(f"{first}_{second}" for first, second in pairwise(tokens))

        vector = [0.0] * self.dim
        for feature, frequency in counts.items():
            index, sign = _bucket(feature, self.dim)
            vector[index] += sign * (1.0 + math.log(frequency))

        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0.0:
            vector = [value / norm for value in vector]
        return vector, len(tokens)


__all__ = ["HashingEmbeddingProvider", "tokenise"]
