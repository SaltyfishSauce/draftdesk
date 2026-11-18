from draftdesk.config import StoreConfig
from draftdesk.extract import find_order_number, identify_customer
from draftdesk.filters import skip_reason
from draftdesk.models import InboundEmail

STORE = StoreConfig(form_senders=["noreply@platform.example"], internal_domains=["mystore.example"])


def mail(**kw):
    base = dict(id="1", from_addr="a@example.com", subject="Hi", body_text="Hello")
    return InboundEmail(**{**base, **kw})


def test_order_number_patterns():
    assert find_order_number("my order #10234 is late") == "10234"
    assert find_order_number("Order Number: 5567") == "5567"
    assert find_order_number("订单号：88231 还没到") == "88231"
    assert find_order_number("pedido número 4410") == "4410"
    assert find_order_number("I ordered 2 mugs") is None


def test_form_body_address_only_trusted_from_form_sender():
    body = "Name: Jo Ray\nEmail: jo@example.com\nMessage: hi"
    form = identify_customer(mail(from_addr="noreply@platform.example", body_text=body), STORE)
    assert (form.email, form.name, form.source) == ("jo@example.com", "Jo Ray", "form_body")
    # The same text from an ordinary sender must not redirect the reply.
    direct = identify_customer(mail(from_addr="mallory@example.net", body_text=body), STORE)
    assert (direct.email, direct.source) == ("mallory@example.net", "from_header")


def test_reply_to_used_for_form_sender_without_body_field():
    m = mail(from_addr="noreply@platform.example", reply_to="Kim@Example.com", body_text="Where is it?")
    assert identify_customer(m, STORE).email == "kim@example.com"


def test_filters():
    assert skip_reason(mail(), STORE) is None
    assert skip_reason(mail(headers={"Auto-Submitted": "auto-replied"}), STORE)
    assert skip_reason(mail(headers={"Precedence": "bulk"}), STORE)
    assert skip_reason(mail(subject="Out of office until Friday"), STORE)
    assert skip_reason(mail(from_addr="no-reply@shipper.example"), STORE) == "automated sender"
    assert skip_reason(mail(from_addr="boss@mystore.example"), STORE) == "internal sender"
    assert skip_reason(mail(body_text="  "), STORE) == "empty body"
    # The platform's form relay is a noreply address but must still be processed.
    assert skip_reason(mail(from_addr="noreply@platform.example"), STORE) is None
