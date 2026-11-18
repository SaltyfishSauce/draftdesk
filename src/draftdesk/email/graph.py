"""Microsoft 365 / Outlook via Microsoft Graph.

Auth modes
- client_credentials: an app registration with application permissions.
  Restrict it to the support mailbox with an Exchange application access policy.
- refresh_token: delegated access as one signed-in user (works for /me and for
  shared mailboxes that user can open).

Permissions: Mail.ReadWrite to read and create drafts; Mail.Send only when
review.mode is 'dashboard'.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from ..models import InboundEmail, ReplyTarget
from .base import EmailProvider
from .mime import html_to_text, text_to_html

GRAPH = "https://graph.microsoft.com/v1.0"
_SELECT = (
    "id,conversationId,internetMessageId,subject,from,replyTo,toRecipients,"
    "body,receivedDateTime,internetMessageHeaders"
)


class GraphEmailProvider(EmailProvider):
    name = "graph"

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str = "",
        refresh_token: str = "",
        mailbox: str = "",
        auth: str = "client_credentials",
        folder: str = "inbox",
        lookback_hours: int = 24,
        page_size: int = 25,
        client: httpx.Client | None = None,
    ):
        if auth == "client_credentials" and not mailbox:
            raise ValueError("graph: 'mailbox' is required with client_credentials auth")
        self._tenant, self._client_id, self._secret = tenant_id, client_id, client_secret
        self._refresh, self._auth = refresh_token, auth
        self._root = f"{GRAPH}/users/{mailbox}" if mailbox else f"{GRAPH}/me"
        self._folder, self._lookback, self._page = folder, lookback_hours, page_size
        self._http = client or httpx.Client(timeout=30)
        self._token, self._expires = "", 0.0

    # -- auth -----------------------------------------------------------------

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        data = {"client_id": self._client_id}
        if self._secret:
            data["client_secret"] = self._secret
        if self._auth == "client_credentials":
            data.update(grant_type="client_credentials", scope="https://graph.microsoft.com/.default")
        else:
            data.update(grant_type="refresh_token", refresh_token=self._refresh)
        resp = self._http.post(
            f"https://login.microsoftonline.com/{self._tenant}/oauth2/v2.0/token", data=data
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._expires = time.time() + int(payload.get("expires_in", 3600))
        # Microsoft rotates refresh tokens; keep the newest for this process.
        self._refresh = payload.get("refresh_token", self._refresh)
        return self._token

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._access_token()}", **kwargs.pop("headers", {})}
        resp = self._http.request(method, url, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp

    # -- read -----------------------------------------------------------------

    @staticmethod
    def _addr(obj: dict | None) -> tuple[str, str]:
        ea = (obj or {}).get("emailAddress") or {}
        return (ea.get("address") or "").lower(), ea.get("name") or ""

    def _to_email(self, item: dict) -> InboundEmail:
        from_addr, from_name = self._addr(item.get("from"))
        reply_to = next((self._addr(r)[0] for r in item.get("replyTo") or []), None)
        body = item.get("body") or {}
        text = body.get("content") or ""
        if (body.get("contentType") or "").lower() == "html":
            text = html_to_text(text)
        wanted = {"auto-submitted", "precedence", "list-unsubscribe"}
        headers = {
            h["name"]: h["value"]
            for h in item.get("internetMessageHeaders") or []
            if h.get("name", "").lower() in wanted
        }
        return InboundEmail(
            id=item["id"],
            thread_id=item.get("conversationId"),
            internet_message_id=item.get("internetMessageId"),
            subject=item.get("subject") or "",
            from_addr=from_addr,
            from_name=from_name,
            reply_to=reply_to,
            to_addrs=[self._addr(r)[0] for r in item.get("toRecipients") or []],
            body_text=text.strip(),
            received_at=item.get("receivedDateTime"),
            headers=headers,
        )

    def fetch_new(self, cursor: str | None) -> tuple[list[InboundEmail], str | None]:
        if not cursor:
            start = datetime.now(timezone.utc) - timedelta(hours=self._lookback)
            cursor = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        url: str | None = f"{self._root}/mailFolders/{self._folder}/messages"
        # 'ge' rather than 'gt': two messages can share a timestamp, and the
        # pipeline already ignores ids it has seen.
        params: dict | None = {
            "$filter": f"receivedDateTime ge {cursor}",
            "$orderby": "receivedDateTime asc",
            "$top": str(self._page),
            "$select": _SELECT,
        }
        mails: list[InboundEmail] = []
        while url:
            resp = self._request(
                "GET", url, params=params, headers={"Prefer": 'outlook.body-content-type="text"'}
            )
            payload = resp.json()
            mails.extend(self._to_email(item) for item in payload.get("value", []))
            url, params = payload.get("@odata.nextLink"), None
        new_cursor = cursor
        if mails and mails[-1].received_at:
            new_cursor = mails[-1].received_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return mails, new_cursor

    # -- write ----------------------------------------------------------------

    def create_reply_draft(self, target: ReplyTarget, body: str) -> str:
        # createReply gives a draft that is threaded and already quotes the
        # original. We override the recipient (contact-form emails come "from"
        # the platform, not the customer) and then put our text above the quote.
        created = self._request(
            "POST",
            f"{self._root}/messages/{target.message_id}/createReply",
            json={"message": {"toRecipients": [{"emailAddress": {"address": target.to_addr}}]}},
        ).json()
        quoted = (created.get("body") or {}).get("content") or ""
        ours = f"<div>{text_to_html(body)}</div><br>"
        lower = quoted.lower()
        start = lower.find("<body")
        if start != -1:
            insert_at = lower.find(">", start) + 1
            content = quoted[:insert_at] + ours + quoted[insert_at:]
        else:
            content = ours + quoted
        self._request(
            "PATCH",
            f"{self._root}/messages/{created['id']}",
            json={"body": {"contentType": "html", "content": content}},
        )
        return created["id"]

    def send_reply(self, target: ReplyTarget, body: str) -> None:
        draft_id = self.create_reply_draft(target, body)
        self._request("POST", f"{self._root}/messages/{draft_id}/send")
