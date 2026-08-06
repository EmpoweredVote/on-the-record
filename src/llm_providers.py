"""Layer-3 speaker-ID model providers: prompt in, completion text out.

A thin seam so any model can be swapped/compared. Prompt-building, the anchoring
guardrail, and parsing live in src/llm_utils.py — providers only call the model.
"""
from __future__ import annotations

import os
from typing import Protocol

import anthropic

from . import config

_SYSTEM_PROMPT = (
    "You identify who is speaking in a transcript. Respond with ONLY the "
    "requested JSON object and nothing else."
)


class SpeakerIDProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        system: "str | None" = None,
    ) -> str:
        ...


class AnthropicProvider:
    """Wraps anthropic.Anthropic() (uses ANTHROPIC_API_KEY)."""

    def __init__(self, model: str, client=None):
        self.name = "anthropic"
        self.model = model
        self._client = client or anthropic.Anthropic()

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        system: "str | None" = None,
    ) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or _SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text


class OpenAICompatProvider:
    """Wraps an OpenAI-compatible chat endpoint (Gemini, Deepseek, Kimi, GLM)."""

    def __init__(self, model: str, base_url: str, api_key: str, client=None):
        self.name = "openai_compat"
        self.model = model
        if client is None:
            from openai import OpenAI

            client = OpenAI(base_url=base_url, api_key=api_key)
        self._client = client

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        system: "str | None" = None,
    ) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system or _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content or ""


def get_provider(name: str) -> SpeakerIDProvider:
    """Construct the provider for a key in config.SPEAKER_ID_MODELS.

    Raises KeyError for an unknown name and RuntimeError when an
    OpenAI-compatible provider's api_key_env is unset.
    """
    cfg = config.SPEAKER_ID_MODELS[name]  # KeyError -> unknown model key
    provider = cfg["provider"]
    if provider == "anthropic":
        return AnthropicProvider(cfg["model"])
    if provider == "openai_compat":
        key = os.environ.get(cfg["api_key_env"])
        if not key:
            raise RuntimeError(
                f"{name}: environment variable {cfg['api_key_env']} is not set"
            )
        return OpenAICompatProvider(cfg["model"], cfg["base_url"], key)
    raise ValueError(f"{name}: unknown provider {provider!r}")


# --- Meeting-pipeline Anthropic-shaped client (summarize/topics/agenda_interpret/
# agenda_align/publish) --------------------------------------------------------
#
# Those modules call client.messages.create(model=..., max_tokens=..., system=...,
# messages=[...]) and read response.content[0].text (and, in a couple of
# truncation-detection spots, response.stop_reason). The client itself is
# injected, constructed at 5 entry points (src/summarize.py, src/publish.py,
# scripts/poll_agendas.py, scripts/backfill_agenda.py,
# scripts/calibrate_alignment.py). make_llm_client() below gives those entry
# points a single seam to swap billing (Anthropic direct vs OpenRouter) without
# touching any call site.

# Model-ID map for the Anthropic-compat adapter: Anthropic API ids -> OpenRouter
# ids. Anything not listed passes through unchanged (so a config value that is
# already an OpenRouter id, e.g. "deepseek/deepseek-chat-v3.1", just works).
_OPENROUTER_MODEL_MAP = {
    "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4.5",
    "claude-sonnet-4-5": "anthropic/claude-sonnet-4.5",
}


class _Text:
    def __init__(self, text: str):
        self.text = text


class _Message:
    def __init__(self, text: str, stop_reason: str):
        self.content = [_Text(text)]
        self.stop_reason = stop_reason


class AnthropicCompatClient:
    """Duck-types the slice of anthropic.Anthropic() the pipeline uses
    (client.messages.create(...) -> response.content[0].text / .stop_reason),
    backed by an OpenAI-compatible endpoint (OpenRouter). Lets every
    client-injected call site switch billing without code changes."""

    def __init__(self, base_url: str, api_key: str, client=None):
        if client is None:
            from openai import OpenAI

            client = OpenAI(base_url=base_url, api_key=api_key)
        self._client = client
        self.messages = self  # so client.messages.create(...) resolves

    def create(self, *, model: str, max_tokens: int, messages: list,
               system: "str | None" = None, temperature: "float | None" = None,
               **_ignored):
        oai_messages = ([{"role": "system", "content": system}] if system else [])
        oai_messages += messages
        kwargs = dict(model=_OPENROUTER_MODEL_MAP.get(model, model),
                      max_tokens=max_tokens, messages=oai_messages)
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        stop_reason = ("max_tokens" if choice.finish_reason == "length"
                       else "end_turn")
        return _Message(choice.message.content or "", stop_reason)


def make_llm_client():
    """The pipeline's Anthropic-shaped client, chosen by config.LLM_CLIENT_BACKEND:
    "anthropic" -> anthropic.Anthropic() (needs ANTHROPIC_API_KEY),
    "openrouter" -> AnthropicCompatClient on OpenRouter (needs OPENROUTER_API_KEY;
    Claude model ids are mapped to their OpenRouter equivalents)."""
    backend = config.LLM_CLIENT_BACKEND
    if backend == "anthropic":
        return anthropic.Anthropic()
    if backend == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError(
                "LLM_CLIENT_BACKEND=openrouter: environment variable "
                "OPENROUTER_API_KEY is not set")
        return AnthropicCompatClient(config._OPENROUTER_URL, key)
    raise ValueError(f"unknown LLM_CLIENT_BACKEND {backend!r}")
