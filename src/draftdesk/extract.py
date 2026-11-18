"""Work out who the customer is and which order they mean.

E-commerce platforms often relay contact-form submissions from their own
address, so the From header is the store, not the customer. Replying to it
would send the answer back to the platform. Resolution order:

1. An "Email:" field inside the body (contact-form layout)
2. The Reply-To header
3. The From header
"""

from __future__ import annotations

import fnmatch
import re

from .config import StoreConfig
from .models import CustomerIdentity, InboundEmail

_EMAIL = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
_FORM_EMAIL = re.compile(
    rf"^\s*(?:e-?mail(?: address)?|邮箱|電郵|correo(?: electr[oó]nico)?)\s*[:：]\s*({_EMAIL})",
    re.I | re.M,
)
_FORM_NAME = re.compile(r"^\s*(?:full name|name|姓名|nombre)\s*[:：]\s*(.+?)\s*$", re.I | re.M)
_ORDER = re.compile(
    r"(?:order|订单|訂單|pedido|bestellung|commande)\s*"
    r"(?:number|num|no\.?|id|号|號|编号|n[úu]mero|nummer)?\s*[:：#]?\s*#?\s*(\d{3,12})",
    re.I,
)


def _is_form_sender(addr: str, store: StoreConfig) -> bool:
    return any(fnmatch.fnmatch(addr.lower(), p.lower()) for p in store.form_senders)


def find_order_number(text: str) -> str | None:
    match = _ORDER.search(text)
    return match.group(1) if match else None


def identify_customer(mail: InboundEmail, store: StoreConfig) -> CustomerIdentity:
    order_number = find_order_number(f"{mail.subject}\n{mail.body_text}")

    # Only trust an "Email:" line in the body when the message came through the
    # platform's form relay. Otherwise any sender could redirect the reply (and
    # another customer's order details) to an address of their choosing.
    if _is_form_sender(mail.from_addr, store):
        email_match = _FORM_EMAIL.search(mail.body_text)
        if email_match:
            name_match = _FORM_NAME.search(mail.body_text)
            return CustomerIdentity(
                email=email_match.group(1).lower(),
                name=name_match.group(1) if name_match else "",
                order_number=order_number,
                source="form_body",
            )
        if mail.reply_to:
            return CustomerIdentity(
                email=mail.reply_to.lower(), order_number=order_number, source="reply_to"
            )

    return CustomerIdentity(
        email=mail.from_addr.lower(),
        name=mail.from_name,
        order_number=order_number,
        source="from_header",
    )
