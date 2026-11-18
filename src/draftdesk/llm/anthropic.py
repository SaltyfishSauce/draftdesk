"""Claude via the official Anthropic SDK.

Credentials come from the environment (ANTHROPIC_API_KEY or an `ant auth login`
profile); nothing is read from the config file.
"""

from __future__ import annotations

import anthropic

from ..models import DraftRequest, DraftResult
from ..prompt import RESULT_SCHEMA, ResponseParseError, build_prompt, parse_result
from .base import LLMError, LLMProvider

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str = "claude-opus-5-5",
        effort: str = "medium",
        max_tokens: int = 16000,
        fallbacks: bool = True,
        client: anthropic.Anthropic | None = None,
    ):
        self._model, self._effort, self._max_tokens = model, effort, max_tokens
        # Server-side fallbacks re-run a request on another model if a safety
        # classifier declines it. Supported on the first-party API only; set
        # `fallbacks: false` for Bedrock, Vertex AI or Foundry deployments and
        # for models that do not accept the parameter.
        self._fallbacks = fallbacks
        self._client = client or anthropic.Anthropic()

    def draft(self, request: DraftRequest) -> DraftResult:
        system, user = build_prompt(request)
        params = dict(
            model=self._model,
            max_tokens=self._max_tokens,
            # The rules and knowledge base are identical for every email, so
            # mark them cacheable; only the per-email user message is new input.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": RESULT_SCHEMA},
            },
        )
        try:
            if self._fallbacks:
                response = self._client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **params
                )
            else:
                response = self._client.messages.create(**params)
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit reached; retry this email from the dashboard") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("could not reach the Anthropic API") from exc

        if response.stop_reason == "refusal":
            raise LLMError("the model declined to draft a reply for this email")
        if response.stop_reason == "max_tokens":
            raise LLMError("the reply was cut off at max_tokens; raise llm.max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return parse_result(text, model=response.model)
        except ResponseParseError as exc:
            raise LLMError(str(exc)) from exc
