"""Portable column types.

``VectorColumn`` lets the same model run on:

* PostgreSQL + pgvector -> a native ``vector(N)`` column queried with the
  cosine-distance operator (``<=>``) inside the database;
* anything else (SQLite in development/CI) -> JSON-encoded float arrays with an
  in-process cosine scan.

The portable fallback is explicit and bounded (see ``SEMANTIC_SCAN_LIMIT``), and
PatchLens reports which backend served a query so results are never mistaken for
approximate-nearest-neighbour search.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity, tolerant of unnormalised input and zero vectors."""
    if len(left) != len(right):
        raise ValueError("vector dimension mismatch")
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


class VectorColumn(TypeDecorator[list[float]]):
    """Storage-adaptive embedding column.

    ``dim=None`` (the default) maps to pgvector's *unconstrained* ``vector`` type
    so ``EMBEDDING_DIM`` stays a deployment setting rather than a schema change:
    the application validates dimensions on write and records the real dimension
    in ``code_embeddings.dim``. If you want an indexable column (HNSW / IVFFlat
    need a fixed dimension) run a migration that alters it to ``vector(N)`` - see
    ``docs/ARCHITECTURE.md`` ("Vector storage").
    """

    impl = Text
    cache_ok = True

    def __init__(self, dim: int | None = None, *args: Any, **kwargs: Any) -> None:
        self.dim = dim
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector

                return dialect.type_descriptor(Vector(self.dim) if self.dim else Vector())
            except ImportError:  # pragma: no cover - pgvector is a declared dependency
                return dialect.type_descriptor(Text())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        vector = [float(item) for item in value]
        if self.dim is not None and len(vector) != self.dim:
            raise ValueError(f"expected embedding of dimension {self.dim}, got {len(vector)}")
        if dialect.name == "postgresql":
            return vector
        return json.dumps(vector, separators=(",", ":"))

    def process_result_value(self, value: Any, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, str):
            return [float(item) for item in json.loads(value)]
        return [float(item) for item in value]


__all__ = ["VectorColumn", "cosine_similarity"]
