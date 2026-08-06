"""Tests for the Layer-3 model provider seam (src.llm_providers)."""
from __future__ import annotations

import pytest

from src import llm_providers


class _FakeAnthropicMessage:
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text})()]


class _FakeAnthropicClient:
    def __init__(self):
        self.messages = self
        self.captured = {}

    def create(self, **kwargs):
        self.captured = kwargs
        return _FakeAnthropicMessage('{"name": null}')


class _FakeChoice:
    def __init__(self, text):
        self.message = type("Msg", (), {"content": text})()


class _FakeOpenAIClient:
    def __init__(self):
        self.chat = self
        self.completions = self
        self.captured = {}

    def create(self, **kwargs):
        self.captured = kwargs
        return type("Resp", (), {"choices": [_FakeChoice('{"name": null}')]})()


def test_anthropic_provider_calls_messages_and_returns_text():
    client = _FakeAnthropicClient()
    p = llm_providers.AnthropicProvider("claude-haiku-4-5-20251001", client=client)
    out = p.complete("who is SPEAKER_00?", max_tokens=150, temperature=0.0)
    assert out == '{"name": null}'
    assert client.captured["model"] == "claude-haiku-4-5-20251001"
    assert client.captured["max_tokens"] == 150


def test_openai_compat_provider_calls_chat_and_returns_text():
    client = _FakeOpenAIClient()
    p = llm_providers.OpenAICompatProvider("deepseek-chat", "https://x", "k", client=client)
    out = p.complete("who is SPEAKER_00?", max_tokens=150, temperature=0.0)
    assert out == '{"name": null}'
    assert client.captured["model"] == "deepseek-chat"


def test_get_provider_openai_compat_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        llm_providers.get_provider("deepseek")


def test_get_provider_unknown_name_raises():
    with pytest.raises(KeyError):
        llm_providers.get_provider("no-such-model")


def test_get_provider_anthropic_returns_anthropic_provider(monkeypatch):
    monkeypatch.setattr(llm_providers.anthropic, "Anthropic", lambda: _FakeAnthropicClient())
    p = llm_providers.get_provider("haiku")
    assert isinstance(p, llm_providers.AnthropicProvider)
    assert p.model == "claude-haiku-4-5-20251001"


class _FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs

        class _Msg:
            content = [type("B", (), {"text": "ok"})()]

        return _Msg()


class _FakeAnthropic:
    def __init__(self):
        self.messages = _FakeMessages()


def test_complete_accepts_custom_system_prompt():
    client = _FakeAnthropic()
    p = llm_providers.AnthropicProvider(model="m", client=client)
    p.complete("hi", max_tokens=10, temperature=0.0, system="You screen videos.")
    assert client.messages.kwargs["system"] == "You screen videos."


def test_complete_defaults_to_speaker_id_system_prompt():
    client = _FakeAnthropic()
    p = llm_providers.AnthropicProvider(model="m", client=client)
    p.complete("hi", max_tokens=10, temperature=0.0)
    assert client.messages.kwargs["system"] == llm_providers._SYSTEM_PROMPT


# --- AnthropicCompatClient / make_llm_client (meeting-pipeline OpenRouter seam) ---


class _FakeORChoice:
    def __init__(self, text, finish_reason="stop"):
        self.message = type("Msg", (), {"content": text})()
        self.finish_reason = finish_reason


class _FakeORClient:
    """Fake OpenAI-shaped client: records kwargs, returns a canned response."""

    def __init__(self, reply_text="OK", finish_reason="stop"):
        self.chat = self
        self.completions = self
        self.captured = None
        self._reply_text = reply_text
        self._finish_reason = finish_reason

    def create(self, **kwargs):
        self.captured = kwargs
        return type("Resp", (), {
            "choices": [_FakeORChoice(self._reply_text, self._finish_reason)],
        })()


def test_anthropic_compat_client_leads_with_system_message():
    fake = _FakeORClient()
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    client.create(model="m", max_tokens=20, system="Be terse.",
                  messages=[{"role": "user", "content": "hi"}])
    assert fake.captured["messages"] == [
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "hi"},
    ]


def test_anthropic_compat_client_omits_system_message_when_not_given():
    fake = _FakeORClient()
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    client.create(model="m", max_tokens=20, messages=[{"role": "user", "content": "hi"}])
    assert fake.captured["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_compat_client_maps_known_sonnet_model_id():
    fake = _FakeORClient()
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    client.create(model="claude-sonnet-4-5", max_tokens=20,
                  messages=[{"role": "user", "content": "hi"}])
    assert fake.captured["model"] == "anthropic/claude-sonnet-4.5"


def test_anthropic_compat_client_maps_known_haiku_model_id():
    fake = _FakeORClient()
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    client.create(model="claude-haiku-4-5-20251001", max_tokens=20,
                  messages=[{"role": "user", "content": "hi"}])
    assert fake.captured["model"] == "anthropic/claude-haiku-4.5"


def test_anthropic_compat_client_unknown_model_passes_through():
    fake = _FakeORClient()
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    client.create(model="deepseek/deepseek-chat-v3.1", max_tokens=20,
                  messages=[{"role": "user", "content": "hi"}])
    assert fake.captured["model"] == "deepseek/deepseek-chat-v3.1"


def test_anthropic_compat_client_max_tokens_passthrough():
    fake = _FakeORClient()
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    client.create(model="m", max_tokens=777, messages=[{"role": "user", "content": "hi"}])
    assert fake.captured["max_tokens"] == 777


def test_anthropic_compat_client_temperature_omitted_when_not_given():
    fake = _FakeORClient()
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    client.create(model="m", max_tokens=10, messages=[{"role": "user", "content": "hi"}])
    assert "temperature" not in fake.captured


def test_anthropic_compat_client_temperature_passthrough_when_given():
    fake = _FakeORClient()
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    client.create(model="m", max_tokens=10, temperature=0.2,
                  messages=[{"role": "user", "content": "hi"}])
    assert fake.captured["temperature"] == 0.2


def test_anthropic_compat_client_returns_text_and_end_turn_stop_reason():
    fake = _FakeORClient(reply_text="hello", finish_reason="stop")
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    resp = client.create(model="m", max_tokens=10, messages=[{"role": "user", "content": "hi"}])
    assert resp.content[0].text == "hello"
    assert resp.stop_reason == "end_turn"


def test_anthropic_compat_client_maps_length_finish_reason_to_max_tokens_stop_reason():
    fake = _FakeORClient(reply_text="truncated...", finish_reason="length")
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    resp = client.create(model="m", max_tokens=10, messages=[{"role": "user", "content": "hi"}])
    assert resp.stop_reason == "max_tokens"


def test_make_llm_client_returns_anthropic_compat_client_for_openrouter(monkeypatch):
    monkeypatch.setattr(llm_providers.config, "LLM_CLIENT_BACKEND", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = llm_providers.make_llm_client()
    assert isinstance(client, llm_providers.AnthropicCompatClient)


def test_make_llm_client_raises_when_openrouter_key_missing(monkeypatch):
    monkeypatch.setattr(llm_providers.config, "LLM_CLIENT_BACKEND", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        llm_providers.make_llm_client()


def test_make_llm_client_returns_anthropic_client_for_anthropic_backend(monkeypatch):
    monkeypatch.setattr(llm_providers.config, "LLM_CLIENT_BACKEND", "anthropic")
    fake_anthropic_client = object()
    monkeypatch.setattr(llm_providers.anthropic, "Anthropic", lambda: fake_anthropic_client)
    client = llm_providers.make_llm_client()
    assert client is fake_anthropic_client


def test_make_llm_client_raises_valueerror_on_unknown_backend(monkeypatch):
    monkeypatch.setattr(llm_providers.config, "LLM_CLIENT_BACKEND", "bogus")
    with pytest.raises(ValueError, match="bogus"):
        llm_providers.make_llm_client()
