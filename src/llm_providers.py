"""Layer-3 speaker-ID model providers: prompt in, completion text out.

A thin seam so any model can be swapped/compared. Prompt-building, the anchoring
guardrail, and parsing live in src/llm_utils.py — providers only call the model.
"""
from __future__ import annotations

import os
from typing import Optional, Protocol

import anthropic

from . import config

_SYSTEM_PROMPT = (
    "You identify who is speaking in a transcript. Respond with ONLY the "
    "requested JSON object and nothing else."
)


class SpeakerIDProvider(Protocol):
    name: str
    model: str

    def complete(self, prompt: str, *, max_tokens: int, temperature: float) -> str:
        ...


class AnthropicProvider:
    """Wraps anthropic.Anthropic() (uses ANTHROPIC_API_KEY)."""

    def __init__(self, model: str, client=None):
        self.name = "anthropic"
        self.model = model
        self._client = client or anthropic.Anthropic()

    def complete(self, prompt: str, *, max_tokens: int, temperature: float) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=_SYSTEM_PROMPT,
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

    def complete(self, prompt: str, *, max_tokens: int, temperature: float) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
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
