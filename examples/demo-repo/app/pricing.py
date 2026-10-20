"""Price lookup used by the demo API.

The table is intentionally in-memory: the demo is about review quality, not about
a pricing backend.
"""

from __future__ import annotations

from decimal import Decimal

DEFAULT_PRICES: dict[str, Decimal] = {
    "coffee": Decimal("9.90"),
    "mug": Decimal("14.00"),
    "beans": Decimal("21.50"),
}


class PriceTable:
    """A read-only price lookup with a replaceable source."""

    def __init__(self, prices: dict[str, Decimal] | None = None) -> None:
        self._prices = dict(prices or DEFAULT_PRICES)

    def lookup(self, sku: str) -> Decimal:
        """Return the price for ``sku``."""
        return self._prices[sku]

    def known_skus(self) -> list[str]:
        """Return every sku in the table, sorted for stable output."""
        return sorted(self._prices)
