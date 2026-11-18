"""Shopify via the GraphQL Admin API.

Needs a custom app access token with the read_orders scope.
"""

from __future__ import annotations

import httpx

from ..models import Order, OrderItem, Shipment
from .base import CommerceProvider

_QUERY = """
query Orders($query: String!, $first: Int!) {
  orders(first: $first, query: $query, sortKey: CREATED_AT, reverse: true) {
    nodes {
      name
      email
      createdAt
      displayFinancialStatus
      displayFulfillmentStatus
      totalPriceSet { shopMoney { amount currencyCode } }
      lineItems(first: 20) { nodes { name quantity } }
      fulfillments { createdAt trackingInfo { company number url } }
    }
  }
}
"""


class ShopifyProvider(CommerceProvider):
    name = "shopify"

    def __init__(
        self,
        shop_domain: str,
        access_token: str,
        api_version: str = "2026-07",
        max_orders: int = 3,
        client: httpx.Client | None = None,
    ):
        self._url = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
        self._headers = {"X-Shopify-Access-Token": access_token}
        self._max = max_orders
        self._http = client or httpx.Client(timeout=30)

    def _search(self, query: str, first: int) -> list[Order]:
        resp = self._http.post(
            self._url,
            headers=self._headers,
            json={"query": _QUERY, "variables": {"query": query, "first": first}},
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            raise RuntimeError(f"Shopify GraphQL error: {payload['errors']}")
        orders = []
        for node in payload["data"]["orders"]["nodes"]:
            money = (node.get("totalPriceSet") or {}).get("shopMoney") or {}
            status = " / ".join(
                s for s in (node.get("displayFinancialStatus"), node.get("displayFulfillmentStatus")) if s
            )
            orders.append(
                Order(
                    number=str(node["name"]).lstrip("#"),
                    status=status,
                    placed_at=node.get("createdAt", ""),
                    total=str(money.get("amount", "")),
                    currency=money.get("currencyCode", ""),
                    customer_email=node.get("email") or "",
                    items=[
                        OrderItem(name=i["name"], quantity=int(i.get("quantity", 1)))
                        for i in (node.get("lineItems") or {}).get("nodes", [])
                    ],
                    shipments=[
                        Shipment(
                            carrier=t.get("company") or "",
                            tracking_number=t.get("number") or "",
                            tracking_url=t.get("url") or "",
                            shipped_at=f.get("createdAt", ""),
                        )
                        for f in node.get("fulfillments") or []
                        for t in f.get("trackingInfo") or []
                    ],
                )
            )
        return orders

    def lookup(self, email: str, order_number: str | None = None) -> list[Order]:
        # Quote the value so characters in the address cannot alter the search syntax.
        safe_email = email.replace("\\", "").replace('"', "")
        orders = self._search(f'email:"{safe_email}"', self._max)
        if order_number and order_number not in {o.number for o in orders}:
            orders.extend(self._search(f"name:#{order_number}", 1))
        return orders
