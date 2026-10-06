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


def test_anthropic_compat_client_rejects_unsupported_kwargs():
    fake = _FakeORClient()
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    with pytest.raises(TypeError, match="stop_sequences"):
        client.create(model="m", max_tokens=10,
                       messages=[{"role": "user", "content": "hi"}],
                       stop_sequences=["STOP"])


def test_make_llm_client_returns_anthropic_compat_client_for_openrouter(monkeypatch):
    monkeypatch.setattr(llm_providers.config, "LLM_CLIENT_BACKEND", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = llm_providers.make_llm_client()
    assert isinstance(client, llm_providers.AnthropicCompatClient)
    assert client.base_url == llm_providers.config._OPENROUTER_URL


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


def test_llm_client_env_key_openrouter(monkeypatch):
    monkeypatch.setattr(llm_providers.config, "LLM_CLIENT_BACKEND", "openrouter")
    assert llm_providers.llm_client_env_key() == "OPENROUTER_API_KEY"


def test_llm_client_env_key_anthropic(monkeypatch):
    monkeypatch.setattr(llm_providers.config, "LLM_CLIENT_BACKEND", "anthropic")
    assert llm_providers.llm_client_env_key() == "ANTHROPIC_API_KEY"


# --- Token accounting (usage capture, call-site logging, truncation warning) ---


class _FakeUsageResp:
    """OpenAI-shaped response with a .usage block."""

    def __init__(self, text="OK", finish_reason="stop", prompt_tokens=120,
                 completion_tokens=30, with_usage=True):
        self.choices = [_FakeORChoice(text, finish_reason)]
        if with_usage:
            self.usage = type("U", (), {"prompt_tokens": prompt_tokens,
                                        "completion_tokens": completion_tokens})()


class _FakeUsageClient:
    def __init__(self, resp):
        self.chat = self
        self.completions = self
        self._resp = resp

    def create(self, **kwargs):
        return self._resp


def _usage_records(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == "src.llm_providers" and r.levelname == "INFO"]


def _truncation_records(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == "src.llm_providers" and r.levelname == "WARNING"]


def test_anthropic_compat_client_puts_usage_on_message():
    fake = _FakeUsageClient(_FakeUsageResp(prompt_tokens=1234, completion_tokens=56))
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    resp = client.create(model="m", max_tokens=100,
                         messages=[{"role": "user", "content": "hi"}])
    assert resp.usage.input_tokens == 1234
    assert resp.usage.output_tokens == 56


def test_anthropic_compat_client_usage_defaults_to_zero_when_absent():
    fake = _FakeUsageClient(_FakeUsageResp(with_usage=False))
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    resp = client.create(model="m", max_tokens=100,
                         messages=[{"role": "user", "content": "hi"}])
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (0, 0)


def test_anthropic_compat_client_logs_usage_with_call_site(caplog):
    caplog.set_level("INFO", logger="src.llm_providers")
    fake = _FakeUsageClient(_FakeUsageResp(prompt_tokens=10, completion_tokens=3))
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    with llm_providers.llm_call_site("topics"):
        client.create(model="claude-haiku-4-5-20251001", max_tokens=100,
                      messages=[{"role": "user", "content": "hi"}])
    [line] = _usage_records(caplog)
    assert "call_site=topics" in line
    assert "model=anthropic/claude-haiku-4.5" in line
    assert "input_tokens=10" in line and "output_tokens=3" in line
    assert _truncation_records(caplog) == []


def test_unlabeled_call_logs_unlabeled_call_site(caplog):
    caplog.set_level("INFO", logger="src.llm_providers")
    fake = _FakeUsageClient(_FakeUsageResp())
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    client.create(model="m", max_tokens=100, messages=[{"role": "user", "content": "hi"}])
    [line] = _usage_records(caplog)
    assert "call_site=unlabeled" in line


def test_anthropic_compat_client_warns_on_truncation(caplog):
    caplog.set_level("INFO", logger="src.llm_providers")
    fake = _FakeUsageClient(_FakeUsageResp(finish_reason="length", completion_tokens=512))
    client = llm_providers.AnthropicCompatClient("https://x", "k", client=fake)
    with llm_providers.llm_call_site("summarize.votes"):
        resp = client.create(model="m", max_tokens=512,
                             messages=[{"role": "user", "content": "hi"}])
    assert resp.stop_reason == "max_tokens"
    [warning] = _truncation_records(caplog)
    assert "max_tokens=512" in warning
    assert "call_site=summarize.votes" in warning


def test_openai_compat_provider_captures_and_logs_usage(caplog):
    caplog.set_level("INFO", logger="src.llm_providers")
    fake = _FakeUsageClient(_FakeUsageResp(text="x", prompt_tokens=77, completion_tokens=8))
    p = llm_providers.OpenAICompatProvider("deepseek-chat", "https://x", "k", client=fake)
    with llm_providers.llm_call_site("discovery.classify"):
        assert p.complete("hi", max_tokens=50, temperature=0.0) == "x"
    assert (p.last_usage.input_tokens, p.last_usage.output_tokens) == (77, 8)
    [line] = _usage_records(caplog)
    assert "call_site=discovery.classify" in line
    assert "model=deepseek-chat" in line
    assert _truncation_records(caplog) == []


def test_openai_compat_provider_warns_on_truncation(caplog):
    caplog.set_level("INFO", logger="src.llm_providers")
    fake = _FakeUsageClient(_FakeUsageResp(finish_reason="length"))
    p = llm_providers.OpenAICompatProvider("m", "https://x", "k", client=fake)
    with llm_providers.llm_call_site("evidence.extract"):
        p.complete("hi", max_tokens=50, temperature=0.0)
    [warning] = _truncation_records(caplog)
    assert "call_site=evidence.extract" in warning


def test_anthropic_provider_captures_usage_and_warns_on_max_tokens(caplog):
    caplog.set_level("INFO", logger="src.llm_providers")

    class _Client:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            return type("M", (), {
                "content": [type("B", (), {"text": "t"})()],
                "stop_reason": "max_tokens",
                "usage": type("U", (), {"input_tokens": 9, "output_tokens": 4})(),
            })()

    p = llm_providers.AnthropicProvider("m", client=_Client())
    with llm_providers.llm_call_site("speaker_id"):
        p.complete("hi", max_tokens=4, temperature=0.0)
    assert (p.last_usage.input_tokens, p.last_usage.output_tokens) == (9, 4)
    assert "call_site=speaker_id" in _usage_records(caplog)[0]
    assert "call_site=speaker_id" in _truncation_records(caplog)[0]


def test_llm_call_site_innermost_label_wins_and_resets():
    get = llm_providers._CALL_SITE.get
    with llm_providers.llm_call_site("outer"):
        with llm_providers.llm_call_site("inner"):
            assert get() == "inner"
        assert get() == "outer"
    assert get() == "unlabeled"


def test_llm_call_site_works_as_decorator():
    @llm_providers.llm_call_site("decorated")
    def f():
        return llm_providers._CALL_SITE.get()

    assert f() == "decorated"
    assert f() == "decorated"  # re-entrant: fresh context per call
    assert llm_providers._CALL_SITE.get() == "unlabeled"


class _StopCall(Exception):
    pass


class _CallSiteRecorder:
    """Anthropic-shaped client that records the active call-site label at
    call time and then stops the caller (so no reply parsing runs)."""

    def __init__(self):
        self.messages = self
        self.seen = None

    def create(self, **kwargs):
        self.seen = llm_providers._CALL_SITE.get()
        raise _StopCall


def _call_sites():
    from types import SimpleNamespace

    from src import agenda_align, agenda_interpret, summarize
    from src.agenda_parse import ParsedItem

    meeting = SimpleNamespace(city="C", meeting_type="council", date="2026-01-01",
                              duration_seconds=0)
    item = ParsedItem(position=1, item_number="1", section="S", section_number=1,
                      title_raw="An item")
    return [
        ("summarize.classify", lambda c: summarize._classify_sections_chunk(c, "x")),
        ("summarize.classify", lambda c: summarize._classify_sections_interview(c, [])),
        ("summarize.synthesize", lambda c: summarize._summarize_discussion(c, "t", "T")),
        ("summarize.synthesize", lambda c: summarize._summarize_interview_topic(c, "t", "T")),
        ("summarize.rollcall", lambda c: summarize._extract_roll_call(c, "t")),
        ("summarize.votes", lambda c: summarize._extract_votes(c, "t")),
        ("summarize.exec", lambda c: summarize._generate_executive_summary(c, [], meeting)),
        ("agenda_interpret", lambda c: agenda_interpret.interpret_item(c, item, "src")),
        ("agenda_align", lambda c: agenda_align.align_items(c, [item], [])),
    ]


def test_pipeline_call_sites_are_labeled():
    for label, call in _call_sites():
        rec = _CallSiteRecorder()
        with pytest.raises(_StopCall):
            call(rec)
        assert rec.seen == label
    assert llm_providers._CALL_SITE.get() == "unlabeled"


def test_provider_call_sites_are_labeled():
    from src.discovery import classify as dclassify
    from src.discovery.models import RawItem
    from src.evidence import crosscheck, extract, judge

    class _Provider:
        def complete(self, prompt, **kw):
            self.seen = llm_providers._CALL_SITE.get()
            raise _StopCall

    import inspect
    raw_params = inspect.signature(RawItem).parameters
    raw = RawItem(**{k: "x" for k, v in raw_params.items()
                     if v.default is inspect.Parameter.empty})
    cases = [
        ("evidence.extract",
         lambda p: extract.extract_quotes("some text", candidate_name="A", provider=p)),
        ("discovery.classify",
         lambda p: dclassify.classify_item(p, raw, race_label="R", roster_names=[])),
    ]
    for label, call in cases:
        prov = _Provider()
        with pytest.raises(_StopCall):
            call(prov)
        assert prov.seen == label
    from src.evidence.models import QuoteCandidate

    cand = QuoteCandidate(text="t", context="c", issue="i")
    cases = [
        ("evidence.crosscheck",
         lambda p: crosscheck.crosscheck(cand, "src", candidate_name="A", provider=p)),
        ("evidence.judge", lambda p: judge.judge(cand, provider=p)),
    ]
    for label, call in cases:
        prov = _Provider()
        with pytest.raises(_StopCall):
            call(prov)
        assert prov.seen == label
