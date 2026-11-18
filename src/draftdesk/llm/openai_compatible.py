"""Any model served behind the OpenAI Chat Completions wire format.

That covers OpenAI itself, Azure OpenAI, and self-hosted or third-party servers
that mirror the format (Ollama, vLLM, LM Studio, and many hosted providers).
Plain HTTP is used so no vendor SDK is required.
"""

from __future__ import annotations

import httpx

from ..models import DraftRequest, DraftResult
from ..prompt import ResponseParseError, build_prompt, parse_result
from .base import LLMError, LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        api_key_header: str = "Authorization",
        json_mode: bool = True,
        extra_params: dict | None = None,
        query_params: dict | None = None,
        timeout: float = 120,
        client: httpx.Client | None = None,
    ):
        self._model, self._url = model, f"{base_url.rstrip('/')}/chat/completions"
        self._headers: dict[str, str] = {}
        if api_key:
            # Azure OpenAI expects the key in an `api-key` header without "Bearer".
            bearer = api_key_header.lower() == "authorization"
            self._headers[api_key_header] = f"Bearer {api_key}" if bearer else api_key
        self._json_mode = json_mode
        self._extra = extra_params or {}
        self._query = query_params or None
        self._http = client or httpx.Client(timeout=timeout)

    def draft(self, request: DraftRequest) -> DraftResult:
        system, user = build_prompt(request)
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            **self._extra,
        }
        if self._json_mode:
            # Not every compatible server supports this; set json_mode: false if
            # yours rejects it. parse_result copes with either.
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = self._http.post(self._url, headers=self._headers, params=self._query, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"model API error {exc.response.status_code}: {exc.response.text[:300]}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach the model API: {exc}") from exc

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise LLMError("the reply was cut off by the model's output limit")
        text = (choice.get("message") or {}).get("content") or ""
        try:
            return parse_result(text, model=data.get("model") or self._model)
        except ResponseParseError as exc:
            raise LLMError(str(exc)) from exc
