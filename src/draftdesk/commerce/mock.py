"""File-backed order data for the demo and for tests."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Order
from .base import CommerceProvider


class MockCommerceProvider(CommerceProvider):
    name = "mock"

    def __init__(self, orders_path: str | Path | None = None, orders: list[dict] | None = None):
        if orders is None:
            if orders_path is None:
                from ..demo_data import path as demo_path

                orders_path = demo_path("orders.json")
            orders = json.loads(Path(orders_path).read_text(encoding="utf-8"))
        self._orders = [Order.model_validate(o) for o in orders]

    def lookup(self, email: str, order_number: str | None = None) -> list[Order]:
        found = [o for o in self._orders if o.customer_email.lower() == email.lower()]
        found.sort(key=lambda o: o.placed_at, reverse=True)
        if order_number and order_number not in {o.number for o in found}:
            found.extend(o for o in self._orders if o.number == order_number)
        return found
