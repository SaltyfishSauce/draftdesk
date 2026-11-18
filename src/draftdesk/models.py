"""Provider-neutral data models shared by every adapter and the pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "order_status", "shipping", "return_refund", "product_question", "complaint", "other"
]


class InboundEmail(BaseModel):
    """One customer email, normalised from whatever the mail provider returns."""

    id: str
    thread_id: str | None = None
    internet_message_id: str | None = None
    subject: str = ""
    from_addr: str = ""
    from_name: str = ""
    reply_to: str | None = None
    to_addrs: list[str] = Field(default_factory=list)
    body_text: str = ""
    received_at: datetime | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class ReplyTarget(BaseModel):
    """Everything a mail provider needs to thread a reply onto the original email."""

    message_id: str
    thread_id: str | None = None
    internet_message_id: str | None = None
    to_addr: str
    subject: str


class Shipment(BaseModel):
    carrier: str = ""
    tracking_number: str = ""
    tracking_url: str = ""
    shipped_at: str = ""


class OrderItem(BaseModel):
    name: str
    quantity: int = 1


class Order(BaseModel):
    number: str
    status: str = ""
    placed_at: str = ""
    total: str = ""
    currency: str = ""
    customer_email: str = ""
    items: list[OrderItem] = Field(default_factory=list)
    shipments: list[Shipment] = Field(default_factory=list)


class CustomerIdentity(BaseModel):
    """Who actually wrote in. Differs from the From header for contact-form emails."""

    email: str
    name: str = ""
    order_number: str | None = None
    source: Literal["form_body", "reply_to", "from_header"] = "from_header"


class DraftRequest(BaseModel):
    """Input handed to an LLM adapter."""

    store_name: str
    signature: str
    knowledge: str
    email: InboundEmail
    customer: CustomerIdentity
    orders: list[Order] = Field(default_factory=list)
    reviewer_instruction: str = ""


class DraftResult(BaseModel):
    """Structured output every LLM adapter must return."""

    body: str
    language: str = "en"
    category: Category = "other"
    needs_human: bool = False
    reviewer_notes: str = ""
    model: str = ""
