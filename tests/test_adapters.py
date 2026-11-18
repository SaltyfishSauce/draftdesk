"""Adapter tests against recorded-shape HTTP responses (httpx.MockTransport).

These verify request construction and response mapping. They do not prove the
adapters work against the live services; see docs/adapters.md.
"""

import base64
import json
from email.message import EmailMessage

import httpx
import pytest

from draftdesk.commerce.bigcommerce import BigCommerceProvider
from draftdesk.commerce.shopify import ShopifyProvider
from draftdesk.commerce.woocommerce import WooCommerceProvider
from draftdesk.email.gmail import GmailEmailProvider
from draftdesk.email.graph import GraphEmailProvider
from draftdesk.email.mime import build_reply, html_to_text, parse_rfc822
from draftdesk.llm.base import LLMError
from draftdesk.llm.openai_compatible import OpenAICompatibleProvider
from draftdesk.models import (
    CustomerIdentity,
    DraftRequest,
    InboundEmail,
    ReplyTarget,
)

TARGET = ReplyTarget(message_id="m1", thread_id="t1", internet_message_id="<orig@x>",
                     to_addr="jo@example.com", subject="Re: Hello")


def client(handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped)), calls


# ---------------------------------------------------------------- Microsoft Graph

def graph_handler(request):
    url = str(request.url)
    if "oauth2/v2.0/token" in url:
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    if request.method == "GET" and "/mailFolders/inbox/messages" in url:
        return httpx.Response(200, json={"value": [{
            "id": "m1", "conversationId": "c1", "internetMessageId": "<a@b>", "subject": "Hello",
            "from": {"emailAddress": {"address": "Jo@Example.com", "name": "Jo"}},
            "replyTo": [], "toRecipients": [{"emailAddress": {"address": "support@shop.example"}}],
            "body": {"contentType": "html", "content": "<p>Where is my order?</p>"},
            "receivedDateTime": "2026-11-09T10:00:00Z",
            "internetMessageHeaders": [{"name": "Auto-Submitted", "value": "no"}, {"name": "X-Other", "value": "1"}],
        }]})
    if url.endswith("/createReply"):
        return httpx.Response(201, json={"id": "d1", "body": {"content": "<html><body><div>quoted</div></body></html>"}})
    if request.method == "PATCH":
        return httpx.Response(200, json={"id": "d1"})
    if url.endswith("/send"):
        return httpx.Response(202)
    return httpx.Response(404)


def test_graph_fetch_and_reply():
    http, calls = client(graph_handler)
    graph = GraphEmailProvider("tenant", "cid", "secret", mailbox="support@shop.example", client=http)
    mails, cursor = graph.fetch_new(None)
    assert mails[0].from_addr == "jo@example.com" and mails[0].body_text == "Where is my order?"
    assert mails[0].headers == {"Auto-Submitted": "no"}
    assert cursor == "2026-11-09T10:00:00Z"

    graph.send_reply(TARGET, "Line one\n<b>not html</b>")
    create = next(c for c in calls if str(c.url).endswith("/createReply"))
    assert json.loads(create.content)["message"]["toRecipients"][0]["emailAddress"]["address"] == "jo@example.com"
    patched = json.loads(next(c for c in calls if c.method == "PATCH").content)["body"]["content"]
    # Our text is escaped, sits above the quoted original, and keeps its line break.
    assert "Line one<br>" in patched and "&lt;b&gt;" in patched
    assert patched.index("Line one") < patched.index("quoted")
    assert str(calls[-1].url).endswith("/users/support@shop.example/messages/d1/send")
    assert sum("oauth2" in str(c.url) for c in calls) == 1  # token is cached


def test_graph_requires_mailbox_for_app_auth():
    with pytest.raises(ValueError):
        GraphEmailProvider("t", "c", "s")


# ---------------------------------------------------------------- Gmail

def test_gmail_fetch_and_draft():
    original = EmailMessage()
    original["From"] = "Jo <jo@example.com>"
    original["Subject"] = "Hello"
    original["Message-ID"] = "<orig@x>"
    original.set_content("Where is my order?")
    raw = base64.urlsafe_b64encode(original.as_bytes()).decode()

    def handler(request):
        url = str(request.url)
        if "oauth2.googleapis.com" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if request.method == "GET" and url.split("?")[0].endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "g1", "threadId": "t1"}]})
        if "/messages/g1" in url:
            return httpx.Response(200, json={"raw": raw, "internalDate": "1794650000000"})
        if url.endswith("/drafts"):
            return httpx.Response(200, json={"id": "draft-9"})
        return httpx.Response(404)

    http, calls = client(handler)
    gmail = GmailEmailProvider("cid", "secret", "refresh", "support@shop.example", client=http)
    mails, cursor = gmail.fetch_new("1794000000")
    assert mails[0].id == "g1" and mails[0].thread_id == "t1" and mails[0].from_addr == "jo@example.com"
    assert cursor == "1794650000"

    assert gmail.create_reply_draft(TARGET, "Thanks!") == "draft-9"
    sent = json.loads(calls[-1].content)["message"]
    assert sent["threadId"] == "t1"
    mime = parse_rfc822(base64.urlsafe_b64decode(sent["raw"]), "x")
    assert mime.to_addrs == ["jo@example.com"] and mime.body_text == "Thanks!"


def test_mime_helpers():
    reply = build_reply("support@shop.example", TARGET, "Hi")
    assert reply["In-Reply-To"] == "<orig@x>" and reply["References"] == "<orig@x>"
    assert html_to_text("<style>p{}</style><p>One</p><p>Two &amp; three<br>four</p>") == "One\nTwo & three\nfour"


# ---------------------------------------------------------------- Commerce

def test_bigcommerce_lookup_handles_empty_collections():
    def handler(request):
        path = request.url.path
        if path.endswith("/v2/orders"):
            assert request.headers["X-Auth-Token"] == "tok"
            return httpx.Response(200, json=[{
                "id": 1041, "status": "Shipped", "date_created": "Mon, 02 Nov 2026 15:20:00 +0000",
                "total_inc_tax": "84.50", "currency_code": "USD", "billing_address": {"email": "jo@example.com"},
            }])
        if path.endswith("/orders/1041/products"):
            return httpx.Response(200, json=[{"name": "Daypack", "quantity": 1}])
        if path.endswith("/orders/1041/shipments"):
            return httpx.Response(204)
        if path.endswith("/orders/777"):
            return httpx.Response(404)
        return httpx.Response(500)

    http, _ = client(handler)
    orders = BigCommerceProvider("hash", "tok", client=http).lookup("jo@example.com", "777")
    assert len(orders) == 1
    assert orders[0].number == "1041" and orders[0].items[0].name == "Daypack" and orders[0].shipments == []


def test_shopify_lookup():
    def handler(request):
        body = json.loads(request.content)
        assert body["variables"]["query"] == 'email:"jo@example.com"'
        return httpx.Response(200, json={"data": {"orders": {"nodes": [{
            "name": "#1001", "email": "jo@example.com", "createdAt": "2026-11-01T00:00:00Z",
            "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "FULFILLED",
            "totalPriceSet": {"shopMoney": {"amount": "20.00", "currencyCode": "USD"}},
            "lineItems": {"nodes": [{"name": "Mug", "quantity": 2}]},
            "fulfillments": [{"createdAt": "2026-11-02T00:00:00Z",
                              "trackingInfo": [{"company": "USPS", "number": "9400ABC", "url": "https://usps.example/t"}]}],
        }]}}})

    http, _ = client(handler)
    order = ShopifyProvider("shop.myshopify.com", "tok", client=http).lookup("jo@example.com")[0]
    assert order.number == "1001" and order.status == "PAID / FULFILLED"
    assert order.shipments[0].tracking_number == "9400ABC"


def test_woocommerce_keeps_only_exact_email_matches():
    def handler(request):
        return httpx.Response(200, json=[
            {"id": 5, "number": "5", "status": "processing", "total": "10.00", "currency": "EUR",
             "billing": {"email": "jo@example.com"}, "line_items": [{"name": "Cap", "quantity": 1}]},
            {"id": 6, "number": "6", "billing": {"email": "jo@example.com.au"}},
        ])

    http, _ = client(handler)
    orders = WooCommerceProvider("https://shop.example", "ck", "cs", client=http).lookup("jo@example.com")
    assert [o.number for o in orders] == ["5"]


# ---------------------------------------------------------------- OpenAI-compatible model

REQUEST = DraftRequest(
    store_name="Shop", signature="Support", knowledge="",
    email=InboundEmail(id="1", from_addr="jo@example.com", subject="Hi", body_text="Where is it?"),
    customer=CustomerIdentity(email="jo@example.com"),
)


def test_openai_compatible_roundtrip_and_errors():
    answer = {"body": "On its way", "language": "en", "category": "shipping", "needs_human": False, "reviewer_notes": ""}

    def ok(request):
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer k"
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        return httpx.Response(200, json={"model": "some-model", "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]})

    http, _ = client(ok)
    result = OpenAICompatibleProvider("some-model", api_key="k", client=http).draft(REQUEST)
    assert result.body == "On its way" and result.model == "some-model"

    http, _ = client(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(LLMError, match="500"):
        OpenAICompatibleProvider("m", client=http).draft(REQUEST)

    http, _ = client(lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "{"}}]}))
    with pytest.raises(LLMError, match="cut off"):
        OpenAICompatibleProvider("m", client=http).draft(REQUEST)
