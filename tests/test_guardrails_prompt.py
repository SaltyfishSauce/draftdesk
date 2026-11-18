import json

import pytest

from draftdesk.guardrails import check_draft
from draftdesk.models import (
    CustomerIdentity,
    DraftRequest,
    DraftResult,
    InboundEmail,
    Order,
    Shipment,
)
from draftdesk.prompt import ResponseParseError, build_prompt, parse_result

ORDER = Order(
    number="1041", customer_email="emma@example.com", status="Shipped",
    shipments=[Shipment(carrier="UPS", tracking_number="1Z999AA10123456784",
                        tracking_url="https://www.ups.com/track?x=1")],
)
MAIL = InboundEmail(id="1", from_addr="emma@example.com", subject="Where is it", body_text="Where is my order?")
KB = "Returns: https://shop.example/returns"


def flags(body, mail=MAIL, orders=(ORDER,), **kw):
    return check_draft(DraftResult(body=body), mail, list(orders), KB, **kw)


def test_clean_draft_has_no_flags():
    body = "Order #1041 shipped with UPS, tracking 1Z999AA10123456784: https://ups.com/track?x=1"
    assert flags(body) == []


def test_invented_facts_are_flagged():
    assert "unverified_link" in flags("See https://evil.example/claim")
    assert "unverified_order_reference" in flags("Your order #9999 is on its way")
    assert "unverified_tracking_number" in flags("Tracking: 1Z000BB20999999999")


def test_knowledge_base_links_are_allowed():
    assert flags("Start here: https://shop.example/returns") == []


def test_context_flags():
    assert flags("Hello", orders=()) == ["no_order_found"]
    assert "order_belongs_to_other_customer" in flags("Hello", orders=(), order_mismatch=True)
    angry = MAIL.model_copy(update={"body_text": "I will call my lawyer and file a chargeback"})
    assert "sensitive_topic" in flags("Hello", mail=angry)


def request(**kw):
    return DraftRequest(
        store_name="Shop", signature="Shop Support", knowledge=KB, email=MAIL,
        customer=CustomerIdentity(email="emma@example.com", name="Emma"), orders=[ORDER], **kw,
    )


def test_prompt_keeps_untrusted_email_out_of_system_prompt():
    system, user = build_prompt(request())
    assert "Where is my order?" not in system
    assert "<customer_email>" in user and "Where is my order?" in user
    assert KB in system
    # The system prompt is identical across emails, which is what makes it cacheable.
    other = request().model_copy(update={"email": MAIL.model_copy(update={"body_text": "Different"})})
    assert build_prompt(other)[0] == system


def test_prompt_includes_order_data_and_instruction():
    _, user = build_prompt(request(reviewer_instruction="be briefer"))
    assert "1Z999AA10123456784" in user and "be briefer" in user


def test_parse_result_variants():
    payload = {"body": "Hi", "language": "en", "category": "shipping", "needs_human": False, "reviewer_notes": ""}
    assert parse_result(json.dumps(payload)).category == "shipping"
    assert parse_result(f"Here you go:\n```json\n{json.dumps(payload)}\n```").body == "Hi"
    assert parse_result(json.dumps({**payload, "category": "nonsense"})).category == "other"
    for bad in ("no json here", "{broken", json.dumps({"body": ""})):
        with pytest.raises(ResponseParseError):
            parse_result(bad)
