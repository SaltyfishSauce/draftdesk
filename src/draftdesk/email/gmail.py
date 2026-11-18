"""Gmail / Google Workspace via the Gmail REST API.

Auth: OAuth refresh token for the support mailbox. Scopes:
gmail.readonly + gmail.compose (drafts and send).
"""

from __future__ import annotations

import base64
import time

import httpx

from ..models import InboundEmail, ReplyTarget
from .base import EmailProvider
from .mime import build_reply, parse_rfc822

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class GmailEmailProvider(EmailProvider):
    name = "gmail"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        address: str,
        query: str = "in:inbox -category:promotions -category:social",
        lookback_hours: int = 24,
        page_size: int = 25,
        client: httpx.Client | None = None,
    ):
        self._client_id, self._secret, self._refresh = client_id, client_secret, refresh_token
        self._address, self._query = address, query
        self._lookback, self._page = lookback_hours, page_size
        self._http = client or httpx.Client(timeout=30)
        self._token, self._expires = "", 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        resp = self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._secret,
                "refresh_token": self._refresh,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._expires = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = self._http.request(
            method, f"{API}{path}", headers={"Authorization": f"Bearer {self._access_token()}"}, **kwargs
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_new(self, cursor: str | None) -> tuple[list[InboundEmail], str | None]:
        # Cursor is epoch seconds. Gmail's `after:` operator has one-second
        # resolution and is inclusive enough to overlap; duplicates are dropped
        # by the pipeline.
        after = int(cursor) if cursor else int(time.time()) - self._lookback * 3600
        ids: list[tuple[str, str]] = []
        page_token: str | None = None
        while True:
            params = {"q": f"{self._query} after:{after}", "maxResults": str(self._page)}
            if page_token:
                params["pageToken"] = page_token
            payload = self._request("GET", "/messages", params=params)
            ids.extend((m["id"], m.get("threadId", "")) for m in payload.get("messages", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        mails: list[tuple[int, InboundEmail]] = []
        for message_id, thread_id in ids:
            item = self._request("GET", f"/messages/{message_id}", params={"format": "raw"})
            raw = base64.urlsafe_b64decode(item["raw"])
            internal_ms = int(item.get("internalDate", "0"))
            mails.append((internal_ms, parse_rfc822(raw, message_id, thread_id or None)))
        mails.sort(key=lambda pair: pair[0])
        newest = max((ms for ms, _ in mails), default=after * 1000)
        return [m for _, m in mails], str(max(after, newest // 1000))

    def _raw(self, target: ReplyTarget, body: str) -> dict:
        mime = build_reply(self._address, target, body)
        message: dict = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()}
        if target.thread_id:
            message["threadId"] = target.thread_id
        return message

    def create_reply_draft(self, target: ReplyTarget, body: str) -> str:
        created = self._request("POST", "/drafts", json={"message": self._raw(target, body)})
        return created["id"]

    def send_reply(self, target: ReplyTarget, body: str) -> None:
        self._request("POST", "/messages/send", json=self._raw(target, body))
