import pytest

from draftdesk.llm.base import LLMError, LLMProvider
from draftdesk.pipeline import ReviewError

from .conftest import by_message


def test_demo_inbox_end_to_end(pipeline):
    summary = pipeline.run_once()
    assert (summary.fetched, summary.drafted, summary.skipped, summary.errors) == (9, 7, 2, 0)
    assert pipeline.store.counts()["pending"] == 7
    assert pipeline.email.sent == []  # nothing is ever sent without approval


def test_second_run_does_not_duplicate(pipeline):
    pipeline.run_once()
    again = pipeline.run_once()
    assert again.fetched == 0
    pipeline.store.set_cursor("email:mock", "0")  # simulate a provider re-delivering mail
    replay = pipeline.run_once()
    assert replay.duplicates == 9 and replay.drafted == 0


def test_shipping_reply_uses_real_tracking_data(pipeline):
    pipeline.run_once()
    draft = by_message(pipeline, "demo-001")
    assert "1Z999AA10123456784" in draft["draft_body"]
    assert draft["flags"] == []
    assert draft["category"] == "shipping"


def test_contact_form_reply_goes_to_the_customer_not_the_platform(pipeline):
    pipeline.run_once()
    draft = by_message(pipeline, "demo-002")
    assert draft["customer_email"] == "li.wei@example.com"
    assert draft["language"] == "zh"
    pipeline.approve_and_send(draft["id"])
    target, _ = pipeline.email.sent[-1]
    assert target.to_addr == "li.wei@example.com"
    assert target.subject.startswith("Re: ")


def test_other_customers_order_is_withheld(pipeline):
    pipeline.run_once()
    draft = by_message(pipeline, "demo-005")
    assert "order_belongs_to_other_customer" in draft["flags"]
    assert draft["orders"] == []
    assert "1Z999AA10987654321" not in draft["draft_body"]


def test_injection_and_sensitive_emails_are_flagged(pipeline):
    pipeline.run_once()
    assert "possible_prompt_injection" in by_message(pipeline, "demo-006")["flags"]
    assert "sensitive_topic" in by_message(pipeline, "demo-004")["flags"]


def test_automated_mail_is_skipped_with_a_reason(pipeline):
    pipeline.run_once()
    assert by_message(pipeline, "demo-008")["detail"] == "auto-submitted message"
    assert by_message(pipeline, "demo-009")["detail"] == "sender on ignore list"


def test_approve_sends_edited_body_once(pipeline):
    pipeline.run_once()
    draft = by_message(pipeline, "demo-001")
    pipeline.approve_and_send(draft["id"], "Edited reply", actor="dana")
    assert pipeline.email.sent[-1][1] == "Edited reply"
    stored = pipeline.store.get(draft["id"])
    assert stored["status"] == "sent" and stored["final_body"] == "Edited reply"
    with pytest.raises(ReviewError):
        pipeline.approve_and_send(draft["id"])
    assert len(pipeline.email.sent) == 1
    actions = [(a["actor"], a["action"], a["detail"]) for a in pipeline.store.audit_for(draft["id"])]
    assert ("dana", "sent", "edited") in actions


def test_failed_send_returns_draft_to_queue(pipeline, monkeypatch):
    pipeline.run_once()
    draft = by_message(pipeline, "demo-001")

    def boom(*_):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(pipeline.email, "send_reply", boom)
    with pytest.raises(ReviewError, match="smtp down"):
        pipeline.approve_and_send(draft["id"])
    assert pipeline.store.get(draft["id"])["status"] == "pending"


def test_reject_and_empty_body(pipeline):
    pipeline.run_once()
    draft = by_message(pipeline, "demo-003")
    with pytest.raises(ReviewError, match="empty"):
        pipeline.approve_and_send(draft["id"], "   ")
    pipeline.reject(draft["id"], "will phone the customer")
    assert pipeline.store.get(draft["id"])["status"] == "rejected"
    assert pipeline.email.sent == []


def test_regenerate_with_instruction(pipeline):
    pipeline.run_once()
    draft = by_message(pipeline, "demo-001")
    pipeline.regenerate(draft["id"], "mention the 24 hour tracking delay")
    assert "24 hour tracking delay" in pipeline.store.get(draft["id"])["draft_body"]


def test_model_failure_is_recorded_and_can_be_retried(pipeline):
    working = pipeline.llm

    class Broken(LLMProvider):
        name = "broken"

        def draft(self, request):
            raise LLMError("model unavailable")

    pipeline.llm = Broken()
    summary = pipeline.run_once()
    assert summary.errors == 7 and summary.drafted == 0
    failed = by_message(pipeline, "demo-001")
    assert failed["status"] == "error" and "model unavailable" in failed["detail"]

    pipeline.llm = working
    pipeline.regenerate(failed["id"])
    assert pipeline.store.get(failed["id"])["status"] == "pending"


def test_mailbox_mode_saves_drafts_instead_of_queueing(pipeline):
    pipeline.config.review.mode = "mailbox"
    pipeline.run_once()
    assert pipeline.store.counts()["in_mailbox"] == 7
    assert len(pipeline.email.drafts) == 7 and pipeline.email.sent == []
