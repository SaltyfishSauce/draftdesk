"""The core workflow: inbox -> filter -> identify -> enrich -> draft -> review -> send."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .commerce.base import CommerceProvider
from .config import Config
from .email.base import EmailProvider
from .extract import identify_customer
from .filters import skip_reason
from .guardrails import check_draft
from .llm.base import LLMProvider
from .models import CustomerIdentity, DraftRequest, InboundEmail, Order, ReplyTarget
from .store import Store

log = logging.getLogger("draftdesk")


class ReviewError(RuntimeError):
    """A reviewer action could not be carried out (wrong state, empty body, ...)."""


@dataclass
class RunSummary:
    fetched: int = 0
    drafted: int = 0
    skipped: int = 0
    errors: int = 0
    duplicates: int = 0
    details: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(
        self,
        config: Config,
        email: EmailProvider,
        commerce: CommerceProvider,
        llm: LLMProvider,
        store: Store,
        knowledge: str = "",
    ):
        self.config = config
        self.email = email
        self.commerce = commerce
        self.llm = llm
        self.store = store
        self.knowledge = knowledge

    # ------------------------------------------------------------------ intake

    def run_once(self) -> RunSummary:
        summary = RunSummary()
        cursor_key = f"email:{self.email.name}"
        mails, new_cursor = self.email.fetch_new(self.store.get_cursor(cursor_key))
        summary.fetched = len(mails)
        for mail in mails:
            outcome = self.process(mail)
            setattr(summary, outcome, getattr(summary, outcome) + 1)
        # The cursor only advances after every message in the batch has a row in
        # the store, so a crash mid-batch re-fetches rather than loses mail.
        if new_cursor:
            self.store.set_cursor(cursor_key, new_cursor)
        log.info(
            "run complete: fetched=%d drafted=%d skipped=%d errors=%d duplicates=%d",
            summary.fetched, summary.drafted, summary.skipped, summary.errors, summary.duplicates,
        )
        return summary

    def process(self, mail: InboundEmail) -> str:
        """Handle one email. Returns 'drafted', 'skipped', 'errors' or 'duplicates'."""
        provider = self.email.name
        if self.store.seen(provider, mail.id):
            return "duplicates"

        base = {"subject": mail.subject, "inbound": mail.model_dump(mode="json")}
        reason = skip_reason(mail, self.config.store)
        if reason:
            draft_id = self.store.add(
                provider, mail.id, "skipped", detail=reason, customer_email=mail.from_addr, **base
            )
            self.store.audit(draft_id, "system", "skipped", reason)
            return "skipped"

        customer = identify_customer(mail, self.config.store)
        base.update(
            customer_email=customer.email,
            customer_name=customer.name,
            customer=customer.model_dump(),
        )
        try:
            orders, mismatch = self._lookup_orders(customer)
            fields = self._draft(mail, customer, orders, mismatch)
        except Exception as exc:  # noqa: BLE001 - one bad email must not stop the batch
            log.exception("failed to draft reply for message %s", mail.id)
            draft_id = self.store.add(
                provider, mail.id, "error", detail=f"{type(exc).__name__}: {exc}", **base
            )
            self.store.audit(draft_id, "system", "error", str(exc))
            return "errors"

        draft_id = self.store.add(provider, mail.id, "pending", **base, **fields)
        self.store.audit(draft_id, "system", "drafted", f"model={fields['model']}")

        if self.config.review.mode == "mailbox":
            try:
                target = self._target(self.store.get(draft_id))
                provider_draft_id = self.email.create_reply_draft(target, fields["draft_body"])
            except Exception as exc:  # noqa: BLE001
                log.exception("could not save draft to mailbox for message %s", mail.id)
                self.store.audit(draft_id, "system", "mailbox_draft_failed", str(exc))
                # Stays 'pending' so it can still be handled from the dashboard.
            else:
                self.store.update(
                    draft_id, status="in_mailbox", provider_draft_id=provider_draft_id or ""
                )
                self.store.audit(draft_id, "system", "saved_to_mailbox", provider_draft_id or "")
        return "drafted"

    def _lookup_orders(self, customer: CustomerIdentity) -> tuple[list[Order], bool]:
        """Fetch the customer's orders. Returns (orders, order_number_mismatch).

        An order is only ever shown to the model when its email matches the
        person writing in. Knowing an order number is not proof of ownership.
        """
        orders = self.commerce.lookup(customer.email, customer.order_number)
        owned = [o for o in orders if o.customer_email.lower() == customer.email.lower()]
        mismatch = bool(customer.order_number) and any(
            o.number.lstrip("#") == customer.order_number for o in orders if o not in owned
        )
        return owned[: self.config.max_orders], mismatch

    def _draft(
        self,
        mail: InboundEmail,
        customer: CustomerIdentity,
        orders: list[Order],
        mismatch: bool,
        instruction: str = "",
    ) -> dict:
        request = DraftRequest(
            store_name=self.config.store.name,
            signature=self.config.store.signature,
            knowledge=self.knowledge,
            email=mail,
            customer=customer,
            orders=orders,
            reviewer_instruction=instruction,
        )
        result = self.llm.draft(request)
        flags = check_draft(
            result, mail, orders, self.knowledge, customer.order_number, order_mismatch=mismatch
        )
        return {
            "draft_body": result.body,
            "category": result.category,
            "language": result.language,
            "reviewer_notes": result.reviewer_notes,
            "flags": flags,
            "orders": [o.model_dump() for o in orders],
            "model": result.model,
        }

    # ------------------------------------------------------------------ review

    @staticmethod
    def _target(draft: dict) -> ReplyTarget:
        inbound = InboundEmail.model_validate(draft["inbound"])
        subject = inbound.subject or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        return ReplyTarget(
            message_id=inbound.id,
            thread_id=inbound.thread_id,
            internet_message_id=inbound.internet_message_id,
            to_addr=draft["customer_email"],
            subject=subject,
        )

    def _require(self, draft_id: int, status: str = "pending") -> dict:
        draft = self.store.get(draft_id)
        if draft is None:
            raise ReviewError(f"draft {draft_id} not found")
        if draft["status"] != status:
            raise ReviewError(f"draft {draft_id} is '{draft['status']}', not '{status}'")
        return draft

    def approve_and_send(self, draft_id: int, body: str | None = None, actor: str = "agent") -> None:
        draft = self._require(draft_id)
        final = (body if body is not None else draft["draft_body"]).strip()
        if not final:
            raise ReviewError("reply body is empty")
        # Claim first so a double click or a second reviewer cannot send twice.
        if not self.store.claim(draft_id, "pending", "sent"):
            raise ReviewError(f"draft {draft_id} was already handled")
        try:
            self.email.send_reply(self._target(draft), final)
        except Exception as exc:
            self.store.update(draft_id, status="pending", decided_at="")
            self.store.audit(draft_id, actor, "send_failed", str(exc))
            raise ReviewError(f"sending failed: {exc}") from exc
        self.store.update(draft_id, final_body=final)
        edited = "edited" if final != draft["draft_body"].strip() else "unedited"
        self.store.audit(draft_id, actor, "sent", edited)

    def reject(self, draft_id: int, reason: str = "", actor: str = "agent") -> None:
        self._require(draft_id)
        if not self.store.claim(draft_id, "pending", "rejected"):
            raise ReviewError(f"draft {draft_id} was already handled")
        self.store.audit(draft_id, actor, "rejected", reason)

    def regenerate(self, draft_id: int, instruction: str = "", actor: str = "agent") -> None:
        """Draft again, optionally with a reviewer instruction.

        Also the retry path for emails whose first attempt failed (status 'error').
        """
        draft = self.store.get(draft_id)
        if draft is None:
            raise ReviewError(f"draft {draft_id} not found")
        if draft["status"] not in ("pending", "error"):
            raise ReviewError(f"draft {draft_id} is '{draft['status']}' and cannot be regenerated")
        mail = InboundEmail.model_validate(draft["inbound"])
        customer = CustomerIdentity.model_validate(draft["customer"])
        try:
            orders, mismatch = self._lookup_orders(customer)
            fields = self._draft(mail, customer, orders, mismatch, instruction)
        except Exception as exc:
            self.store.audit(draft_id, actor, "regenerate_failed", str(exc))
            raise ReviewError(f"regeneration failed: {exc}") from exc
        self.store.update(draft_id, status="pending", detail="", **fields)
        self.store.audit(draft_id, actor, "regenerated", instruction)
