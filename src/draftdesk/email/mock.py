"""File-backed mailbox for the demo and for tests. No network access."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import InboundEmail, ReplyTarget
from .base import EmailProvider


class MockEmailProvider(EmailProvider):
    name = "mock"

    def __init__(self, inbox_path: str | Path | None = None, emails: list[dict] | None = None):
        if emails is None:
            if inbox_path is None:
                from ..demo_data import path as demo_path

                inbox_path = demo_path("emails.json")
            emails = json.loads(Path(inbox_path).read_text(encoding="utf-8"))
        self._emails = [InboundEmail.model_validate(e) for e in emails]
        self.drafts: list[tuple[ReplyTarget, str]] = []
        self.sent: list[tuple[ReplyTarget, str]] = []

    def fetch_new(self, cursor: str | None) -> tuple[list[InboundEmail], str | None]:
        start = int(cursor) if cursor else 0
        return self._emails[start:], str(len(self._emails))

    def create_reply_draft(self, target: ReplyTarget, body: str) -> str:
        self.drafts.append((target, body))
        return f"mock-draft-{len(self.drafts)}"

    def send_reply(self, target: ReplyTarget, body: str) -> None:
        self.sent.append((target, body))
