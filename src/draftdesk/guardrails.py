"""Deterministic checks that run on every draft, independent of the model.

The model's own `needs_human` flag is useful but not sufficient: these checks
do not depend on the model noticing its own mistakes. Each returns a short flag
shown to the reviewer next to the draft.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import DraftResult, InboundEmail, Order

_URL = re.compile(r"https?://[^\s<>\")\]]+", re.I)
_ORDER_REF = re.compile(r"#\s?(\d{3,12})\b")

_SENSITIVE = re.compile(
    r"refund|chargeback|charge back|dispute|lawyer|attorney|legal action|sue\b|lawsuit|"
    r"fraud|scam|bbb|trading standards|injur|allerg|unsafe|"
    r"退款|退货|投诉|律师|起诉|欺诈|reembolso|devoluci[oó]n|abogado|denuncia",
    re.I,
)
_INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above|your) (instructions|rules|prompt)|"
    r"disregard (all |the )?(previous|prior|above)|system prompt|you are now|"
    r"as an ai|developer mode|忽略(之前|以上|上述)的?(指令|规则|提示)",
    re.I,
)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def allowed_hosts(knowledge: str, orders: list[Order]) -> set[str]:
    hosts = {_host(u) for u in _URL.findall(knowledge)}
    for order in orders:
        hosts.update(_host(s.tracking_url) for s in order.shipments if s.tracking_url)
    hosts.discard("")
    return hosts


def check_draft(
    result: DraftResult,
    mail: InboundEmail,
    orders: list[Order],
    knowledge: str,
    order_number_mentioned: str | None = None,
    order_mismatch: bool = False,
) -> list[str]:
    flags: list[str] = []
    inbound = f"{mail.subject}\n{mail.body_text}"

    if result.needs_human:
        flags.append("model_requested_review")
    if _SENSITIVE.search(inbound):
        flags.append("sensitive_topic")
    if _INJECTION.search(inbound):
        flags.append("possible_prompt_injection")
    if order_mismatch:
        flags.append("order_belongs_to_other_customer")
    elif not orders:
        flags.append("no_order_found")

    # Links: anything not present in the knowledge base or order data was
    # either invented by the model or copied from the customer's email.
    hosts = allowed_hosts(knowledge, orders)
    if any(_host(u) not in hosts for u in _URL.findall(result.body)):
        flags.append("unverified_link")

    # Order numbers quoted in the reply must exist in the data we looked up
    # (or be the one the customer mentioned themselves).
    known = {o.number.lstrip("#") for o in orders}
    if order_number_mentioned:
        known.add(order_number_mentioned)
    if any(ref not in known for ref in _ORDER_REF.findall(result.body)):
        flags.append("unverified_order_reference")

    # Tracking numbers must come from the order data.
    tracking = {s.tracking_number for o in orders for s in o.shipments if s.tracking_number}
    for token in re.findall(r"\b[A-Z0-9]{10,30}\b", result.body):
        if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
            if token not in tracking:
                flags.append("unverified_tracking_number")
                break

    return flags
