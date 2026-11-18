"""BigCommerce via the V2 Orders API.

Needs a store-level API account with read-only Orders scope.
"""

from __future__ import annotations

import httpx

from ..models import Order, OrderItem, Shipment
from .base import CommerceProvider


class BigCommerceProvider(CommerceProvider):
    name = "bigcommerce"

    def __init__(
        self,
        store_hash: str,
        access_token: str,
        max_orders: int = 3,
        client: httpx.Client | None = None,
    ):
        self._base = f"https://api.bigcommerce.com/stores/{store_hash}/v2"
        self._headers = {"X-Auth-Token": access_token, "Accept": "application/json"}
        self._max = max_orders
        self._http = client or httpx.Client(timeout=30)

    def _get(self, path: str, **params) -> list | dict | None:
        resp = self._http.get(f"{self._base}{path}", headers=self._headers, params=params or None)
        # BigCommerce answers 204 for an empty collection and 404 for an unknown id.
        if resp.status_code in (204, 404):
            return None
        resp.raise_for_status()
        return resp.json()

    def _order(self, raw: dict) -> Order:
        order_id = raw["id"]
        products = self._get(f"/orders/{order_id}/products") or []
        shipments = self._get(f"/orders/{order_id}/shipments") or []
        return Order(
            number=str(order_id),
            status=raw.get("status", ""),
            placed_at=raw.get("date_created", ""),
            total=str(raw.get("total_inc_tax", "")),
            currency=raw.get("currency_code", ""),
            customer_email=(raw.get("billing_address") or {}).get("email", ""),
            items=[OrderItem(name=p.get("name", ""), quantity=int(p.get("quantity", 1))) for p in products],
            shipments=[
                Shipment(
                    carrier=s.get("shipping_provider") or s.get("tracking_carrier") or "",
                    tracking_number=s.get("tracking_number", ""),
                    tracking_url=s.get("tracking_link") or "",
                    shipped_at=s.get("date_created", ""),
                )
                for s in shipments
            ],
        )

    def lookup(self, email: str, order_number: str | None = None) -> list[Order]:
        raw = self._get("/orders", email=email, sort="date_created:desc", limit=self._max) or []
        orders = [self._order(o) for o in raw]
        if order_number and order_number not in {o.number for o in orders}:
            single = self._get(f"/orders/{order_number}")
            if single:
                orders.append(self._order(single))
        return orders
