"""The contract every e-commerce platform adapter implements."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Order


class CommerceProvider(ABC):
    name: str = "commerce"

    @abstractmethod
    def lookup(self, email: str, order_number: str | None = None) -> list[Order]:
        """Return the customer's recent orders, newest first.

        When `order_number` is given, include that order too even if it belongs
        to a different email. Adapters must always fill `Order.customer_email`:
        the pipeline uses it to withhold orders that are not the sender's.
        Read-only API credentials are sufficient and recommended.
        """


class NoCommerceProvider(CommerceProvider):
    """Used when no store API is connected: replies rely on the knowledge base only."""

    name = "none"

    def lookup(self, email: str, order_number: str | None = None) -> list[Order]:
        return []
