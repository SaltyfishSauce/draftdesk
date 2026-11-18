"""Decide which inbound emails deserve a drafted reply.

Returning a reason string means "skip"; None means "draft a reply".
"""

from __future__ import annotations

import fnmatch
import re

from .config import StoreConfig
from .models import InboundEmail

_AUTOMATED_LOCALPARTS = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon|postmaster|bounce[s]?|notifications?)\b",
    re.I,
)
_AUTO_SUBJECTS = re.compile(
    r"^(automatic reply|auto[- ]?reply|out of (the )?office|undeliverable|"
    r"delivery status notification|自动回复|自動回覆)",
    re.I,
)


def _matches(value: str, patterns: list[str]) -> bool:
    value = value.lower()
    return any(fnmatch.fnmatch(value, p.lower()) for p in patterns)


def skip_reason(mail: InboundEmail, store: StoreConfig) -> str | None:
    headers = {k.lower(): v for k, v in mail.headers.items()}
    sender = mail.from_addr.lower()
    is_form_sender = _matches(sender, store.form_senders)

    auto_submitted = headers.get("auto-submitted", "no").lower()
    if auto_submitted not in ("", "no"):
        return "auto-submitted message"
    if headers.get("precedence", "").lower() in {"bulk", "junk", "list"}:
        return "bulk or list mail"
    if "list-unsubscribe" in headers and not is_form_sender:
        return "mailing list"
    if _AUTO_SUBJECTS.match(mail.subject.strip()):
        return "automatic reply"
    if _matches(sender, store.ignore_senders):
        return "sender on ignore list"
    if any(fnmatch.fnmatch(mail.subject.lower(), p.lower()) for p in store.ignore_subjects):
        return "subject on ignore list"
    # A platform's contact-form relay often sends from a noreply@ address, so the
    # form-sender allowlist is checked before the automated-sender rule.
    if not is_form_sender:
        local, _, domain = sender.partition("@")
        if _AUTOMATED_LOCALPARTS.match(local):
            return "automated sender"
        if domain and domain in {d.lower() for d in store.internal_domains}:
            return "internal sender"
    if not mail.body_text.strip():
        return "empty body"
    return None
