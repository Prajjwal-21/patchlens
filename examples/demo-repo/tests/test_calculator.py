"""Tests for the demo calculator.

Deliberately incomplete: ``mean`` and ``apply_discount`` are untested, which is
what the pull request under review adds.
"""

from __future__ import annotations

import pytest

from app.calculator import add, divide


def test_add_returns_the_sum() -> None:
    assert add(2, 3) == 5


def test_add_is_commutative() -> None:
    assert add(-4, 4) == add(4, -4)


def test_divide_returns_the_quotient() -> None:
    assert divide(9, 3) == 3


def test_divide_by_zero_is_undefined() -> None:
    with pytest.raises(ZeroDivisionError):
        divide(1, 0)
