"""A tiny HTTP surface over the calculator, used to give retrieval real depth."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.calculator import add, apply_discount, divide
from app.pricing import PriceTable


@dataclass(slots=True)
class Request:
    path: str
    query: dict[str, str] = field(default_factory=dict)


def handle_sum(request: Request) -> float:
    """Handle ``/sum?a=..&b=..``."""
    left = float(request.query["a"])
    right = float(request.query["b"])
    return float(add(int(left), int(right)))


def handle_divide(request: Request) -> float:
    """Handle ``/divide?a=..&b=..``."""
    return divide(float(request.query["a"]), float(request.query["b"]))


def handle_discount(request: Request, table: PriceTable) -> str:
    """Handle ``/discount?sku=..&percent=..``."""
    price = table.lookup(request.query["sku"])
    return str(apply_discount(price, int(request.query["percent"])))
