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
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        llm_providers.get_provider("deepseek")


def test_get_provider_unknown_name_raises():
    with pytest.raises(KeyError):
        llm_providers.get_provider("no-such-model")


def test_get_provider_anthropic_returns_anthropic_provider(monkeypatch):
    monkeypatch.setattr(llm_providers.anthropic, "Anthropic", lambda: _FakeAnthropicClient())
    p = llm_providers.get_provider("haiku")
    assert isinstance(p, llm_providers.AnthropicProvider)
    assert p.model == "claude-haiku-4-5-20251001"
