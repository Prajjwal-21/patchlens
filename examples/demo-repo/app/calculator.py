"""Arithmetic helpers for the demo service."""

from __future__ import annotations

from decimal import Decimal


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def divide(a: float, b: float) -> float:
    """Return a divided by b.

    The naive version: the caller is expected to have checked ``b``.
    """
    return a / b


def mean(values: list[float]) -> float:
    """Return the arithmetic mean."""
    return sum(values) / len(values)


def apply_discount(price: Decimal, percent: int) -> Decimal:
    """Apply an integer percentage discount to a price."""
    factor = Decimal(100 - percent) / Decimal(100)
    return (price * factor).quantize(Decimal("0.01"))
