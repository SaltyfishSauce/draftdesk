"""WooCommerce via the REST API (wc/v3).

Needs a read-only consumer key and secret. Tracking data is not part of core
WooCommerce; this adapter reads the metadata written by the common
"Shipment Tracking" extension when present.
"""

from __future__ import annotations

import httpx

from ..models import Order, OrderItem, Shipment
from .base import CommerceProvider


class WooCommerceProvider(CommerceProvider):
    name = "woocommerce"

    def __init__(
        self,
        site_url: str,
        consumer_key: str,
        consumer_secret: str,
        max_orders: int = 3,
        client: httpx.Client | None = None,
    ):
        self._base = f"{site_url.rstrip('/')}/wp-json/wc/v3"
        self._auth = (consumer_key, consumer_secret)
        self._max = max_orders
        self._http = client or httpx.Client(timeout=30)

    def _get(self, path: str, **params) -> list | dict | None:
        resp = self._http.get(f"{self._base}{path}", auth=self._auth, params=params or None)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _order(raw: dict) -> Order:
        shipments = []
        for meta in raw.get("meta_data") or []:
            if meta.get("key") == "_wc_shipment_tracking_items":
                for item in meta.get("value") or []:
                    shipments.append(
                        Shipment(
                            carrier=item.get("tracking_provider") or item.get("custom_tracking_provider") or "",
                            tracking_number=item.get("tracking_number", ""),
                            tracking_url=item.get("custom_tracking_link") or "",
                            shipped_at=str(item.get("date_shipped", "")),
                        )
                    )
        return Order(
            number=str(raw.get("number") or raw["id"]),
            status=raw.get("status", ""),
            placed_at=raw.get("date_created", ""),
            total=str(raw.get("total", "")),
            currency=raw.get("currency", ""),
            customer_email=(raw.get("billing") or {}).get("email", ""),
            items=[
                OrderItem(name=i.get("name", ""), quantity=int(i.get("quantity", 1)))
                for i in raw.get("line_items") or []
            ],
            shipments=shipments,
        )

    def lookup(self, email: str, order_number: str | None = None) -> list[Order]:
        # `search` is a fuzzy match across several fields, so keep only exact
        # billing-email matches.
        raw = self._get("/orders", search=email, per_page=20, orderby="date", order="desc") or []
        orders = [self._order(o) for o in raw if (o.get("billing") or {}).get("email", "").lower() == email.lower()]
        orders = orders[: self._max]
        if order_number and order_number not in {o.number for o in orders}:
            single = self._get(f"/orders/{order_number}")
            if single:
                orders.append(self._order(single))
        return orders
