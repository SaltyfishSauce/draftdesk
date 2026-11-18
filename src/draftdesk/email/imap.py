"""Any mailbox that speaks IMAP + SMTP (Zoho, Fastmail, Yahoo, cPanel hosts, ...).

Use an app-specific password where the provider offers one.
"""

from __future__ import annotations

import imaplib
import smtplib
import ssl
import time

from ..models import InboundEmail, ReplyTarget
from .base import EmailProvider
from .mime import build_reply, parse_rfc822


class ImapEmailProvider(EmailProvider):
    name = "imap"

    def __init__(
        self,
        imap_host: str,
        smtp_host: str,
        username: str,
        password: str,
        address: str = "",
        imap_port: int = 993,
        smtp_port: int = 587,
        inbox: str = "INBOX",
        drafts_folder: str = "Drafts",
        first_run_limit: int = 20,
    ):
        self._imap_host, self._imap_port = imap_host, int(imap_port)
        self._smtp_host, self._smtp_port = smtp_host, int(smtp_port)
        self._user, self._password = username, password
        self._address = address or username
        self._inbox, self._drafts = inbox, drafts_folder
        self._first_run_limit = first_run_limit

    def _connect(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(self._imap_host, self._imap_port, ssl_context=ssl.create_default_context())
        conn.login(self._user, self._password)
        return conn

    def fetch_new(self, cursor: str | None) -> tuple[list[InboundEmail], str | None]:
        conn = self._connect()
        try:
            conn.select(self._inbox, readonly=True)
            if cursor:
                last = int(cursor)
                _, data = conn.uid("SEARCH", None, f"UID {last + 1}:*")
                # "n:*" always matches the newest message, even when its UID is
                # below n, so filter explicitly.
                uids = [int(u) for u in data[0].split() if int(u) > last]
            else:
                last = 0
                _, data = conn.uid("SEARCH", None, "ALL")
                uids = [int(u) for u in data[0].split()][-self._first_run_limit :]
            mails: list[InboundEmail] = []
            for uid in sorted(uids):
                _, parts = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw:
                    mails.append(parse_rfc822(raw, message_id=str(uid)))
            return mails, str(max([last, *uids]))
        finally:
            try:
                conn.logout()
            except imaplib.IMAP4.error:
                pass

    def create_reply_draft(self, target: ReplyTarget, body: str) -> str:
        mime = build_reply(self._address, target, body)
        conn = self._connect()
        try:
            status, data = conn.append(
                self._drafts, "(\\Draft)", imaplib.Time2Internaldate(time.time()), mime.as_bytes()
            )
            if status != "OK":
                raise RuntimeError(f"IMAP APPEND to '{self._drafts}' failed: {data!r}")
        finally:
            conn.logout()
        return str(mime["Message-ID"])

    def send_reply(self, target: ReplyTarget, body: str) -> None:
        mime = build_reply(self._address, target, body)
        context = ssl.create_default_context()
        if self._smtp_port == 465:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(self._smtp_host, self._smtp_port, context=context)
        else:
            smtp = smtplib.SMTP(self._smtp_host, self._smtp_port)
            smtp.starttls(context=context)
        with smtp:
            smtp.login(self._user, self._password)
            smtp.send_message(mime)
