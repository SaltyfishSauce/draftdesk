import json
from types import SimpleNamespace

import pytest

from draftdesk.llm.anthropic import AnthropicProvider
from draftdesk.llm.base import LLMError

from .test_adapters import REQUEST


class FakeMessages:
    def __init__(self, response):
        self.response, self.calls = response, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_client(stop_reason="end_turn", text=None):
    text = text or json.dumps(
        {"body": "Hi Jo", "language": "en", "category": "other", "needs_human": False, "reviewer_notes": ""}
    )
    response = SimpleNamespace(
        stop_reason=stop_reason, model="claude-opus-5-5",
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )
    plain, beta = FakeMessages(response), FakeMessages(response)
    return SimpleNamespace(messages=plain, beta=SimpleNamespace(messages=beta)), plain, beta


def test_request_shape_with_fallbacks():
    client, plain, beta = fake_client()
    result = AnthropicProvider(client=client).draft(REQUEST)
    assert result.body == "Hi Jo" and result.model == "claude-opus-5-5"
    assert plain.calls == []
    call = beta.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in call and "temperature" not in call


def test_fallbacks_can_be_disabled():
    client, plain, beta = fake_client()
    AnthropicProvider(client=client, fallbacks=False, model="claude-haiku-5-5").draft(REQUEST)
    assert beta.calls == [] and plain.calls[0]["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in plain.calls[0]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_responses_raise(stop_reason):
    client, _, _ = fake_client(stop_reason=stop_reason)
    with pytest.raises(LLMError):
        AnthropicProvider(client=client).draft(REQUEST)
