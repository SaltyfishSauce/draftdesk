"""The contract every mail provider adapter implements."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import InboundEmail, ReplyTarget


class EmailProvider(ABC):
    #: Short identifier, used as the namespace for message ids and cursors.
    name: str = "email"

    @abstractmethod
    def fetch_new(self, cursor: str | None) -> tuple[list[InboundEmail], str | None]:
        """Return emails that arrived after `cursor`, oldest first, and the new cursor.

        The cursor is an opaque string owned by the adapter (a timestamp, a UID,
        a history id). `None` means first run. Returning a message twice is
        harmless: the pipeline de-duplicates on message id.
        """

    @abstractmethod
    def create_reply_draft(self, target: ReplyTarget, body: str) -> str:
        """Save a reply in the mailbox's Drafts folder. Returns the provider's draft id."""

    @abstractmethod
    def send_reply(self, target: ReplyTarget, body: str) -> None:
        """Send a reply threaded onto the original message."""
