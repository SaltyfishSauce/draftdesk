"""RFC 5322 helpers shared by the Gmail and IMAP adapters."""

from __future__ import annotations

import email
import html
import re
from datetime import timezone
from email.message import EmailMessage, Message
from email.policy import default as default_policy
from email.utils import formatdate, getaddresses, make_msgid, parseaddr, parsedate_to_datetime

from ..models import InboundEmail, ReplyTarget

_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</(p|div|tr|li|h[1-6])\s*>|<br\s*/?>", re.I)
_DROP = re.compile(r"<(script|style|head)\b.*?</\1\s*>", re.I | re.S)


def html_to_text(markup: str) -> str:
    text = _DROP.sub("", markup)
    text = _BLOCK.sub("\n", text)
    text = html.unescape(_TAG.sub("", text))
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def text_to_html(text: str) -> str:
    return html.escape(text).replace("\n", "<br>\n")


def _body_text(msg: Message) -> str:
    plain = html_part = None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type()
        if kind == "text/plain" and plain is None:
            plain = part
        elif kind == "text/html" and html_part is None:
            html_part = part
    chosen = plain or html_part
    if chosen is None:
        return ""
    payload = chosen.get_payload(decode=True) or b""
    text = payload.decode(chosen.get_content_charset() or "utf-8", errors="replace")
    return text.strip() if chosen is plain else html_to_text(text)


def parse_rfc822(raw: bytes, message_id: str, thread_id: str | None = None) -> InboundEmail:
    msg = email.message_from_bytes(raw, policy=default_policy)
    from_name, from_addr = parseaddr(str(msg.get("From", "")))
    _, reply_to = parseaddr(str(msg.get("Reply-To", "")))
    received = None
    if msg.get("Date"):
        try:
            received = parsedate_to_datetime(str(msg["Date"]))
            if received.tzinfo is None:
                received = received.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            received = None
    keep = ("Auto-Submitted", "Precedence", "List-Unsubscribe", "References")
    return InboundEmail(
        id=message_id,
        thread_id=thread_id,
        internet_message_id=str(msg.get("Message-ID", "")).strip() or None,
        subject=str(msg.get("Subject", "")),
        from_addr=from_addr.lower(),
        from_name=from_name,
        reply_to=reply_to.lower() or None,
        to_addrs=[a.lower() for _, a in getaddresses([str(msg.get("To", ""))]) if a],
        body_text=_body_text(msg),
        received_at=received,
        headers={h: str(msg[h]) for h in keep if msg.get(h)},
    )


def build_reply(from_addr: str, target: ReplyTarget, body: str) -> EmailMessage:
    """Build a reply with the headers mail clients use to thread conversations."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = target.to_addr
    msg["Subject"] = target.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=from_addr.rpartition("@")[2] or None)
    if target.internet_message_id:
        msg["In-Reply-To"] = target.internet_message_id
        msg["References"] = target.internet_message_id
    msg.set_content(body)
    return msg
