"""Layer-3 speaker-ID model providers: prompt in, completion text out.

A thin seam so any model can be swapped/compared. Prompt-building, the anchoring
guardrail, and parsing live in src/llm_utils.py — providers only call the model.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import os
from typing import Protocol

import anthropic

from . import config

logger = logging.getLogger(__name__)

# --- Token accounting ----------------------------------------------------------
#
# Every adapter below logs one INFO line per call (call site, model, input and
# output tokens) and a WARNING when the reply was cut off at max_tokens. The call
# site comes from a context variable, not a kwarg, so no call signature (and no
# prompt) changes: wrap the call, or decorate the function that makes it, with
# llm_call_site("summarize.classify"). The innermost label wins.

_CALL_SITE: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "llm_call_site", default="unlabeled")


@contextlib.contextmanager
def llm_call_site(name: str):
    """Label the LLM calls made inside this block (or decorated function)."""
    token = _CALL_SITE.set(name)
    try:
        yield
    finally:
        _CALL_SITE.reset(token)


class Usage:
    """Token counts, shaped like anthropic's response.usage."""

    def __init__(self, input_tokens: int = 0, output_tokens: int = 0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def __repr__(self) -> str:
        return f"Usage(input_tokens={self.input_tokens}, output_tokens={self.output_tokens})"


def _openai_usage(resp) -> Usage:
    """resp.usage (prompt_tokens/completion_tokens) -> Usage; 0 when absent."""
    u = getattr(resp, "usage", None)
    return Usage(getattr(u, "prompt_tokens", None) or 0,
                 getattr(u, "completion_tokens", None) or 0)


def _log_usage(model: str, usage: Usage, truncated: bool, max_tokens: int) -> None:
    site = _CALL_SITE.get()
    logger.info("llm_usage call_site=%s model=%s input_tokens=%d output_tokens=%d",
                site, model, usage.input_tokens, usage.output_tokens)
    if truncated:
        logger.warning("llm reply truncated at max_tokens=%d: call_site=%s model=%s "
                       "output_tokens=%d", max_tokens, site, model, usage.output_tokens)

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
        u = getattr(msg, "usage", None)
        self.last_usage = Usage(getattr(u, "input_tokens", None) or 0,
                                getattr(u, "output_tokens", None) or 0)
        _log_usage(self.model, self.last_usage,
                   getattr(msg, "stop_reason", None) == "max_tokens", max_tokens)
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
        choice = resp.choices[0]
        self.last_usage = _openai_usage(resp)
        _log_usage(self.model, self.last_usage,
                   getattr(choice, "finish_reason", None) == "length", max_tokens)
        return choice.message.content or ""


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
# messages=[...]) and read response.content[0].text. response.stop_reason and
# response.usage (input_tokens/output_tokens) are supported for SDK-shape
# fidelity; the adapter itself logs both (see "Token accounting" above). The client
# itself is injected, constructed at entry points across src/summarize.py,
# src/publish.py, run_local.py, scripts/poll_agendas.py,
# scripts/backfill_agenda.py, scripts/calibrate_alignment.py. make_llm_client()
# below gives those entry points a single seam to swap billing (Anthropic
# direct vs OpenRouter) without touching any call site.

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
    def __init__(self, text: str, stop_reason: str, usage: "Usage | None" = None):
        self.content = [_Text(text)]
        self.stop_reason = stop_reason
        self.usage = usage or Usage()


class AnthropicCompatClient:
    """Duck-types the slice of anthropic.Anthropic() the pipeline uses
    (client.messages.create(...) -> response.content[0].text / .stop_reason /
    .usage),
    backed by an OpenAI-compatible endpoint (OpenRouter). Lets every
    client-injected call site switch billing without code changes."""

    def __init__(self, base_url: str, api_key: str, client=None):
        self.base_url = base_url
        if client is None:
            from openai import OpenAI

            client = OpenAI(base_url=base_url, api_key=api_key)
        self._client = client
        self.messages = self  # so client.messages.create(...) resolves

    def create(self, *, model: str, max_tokens: int, messages: list,
               system: "str | None" = None, temperature: "float | None" = None,
               **extra):
        if extra:
            raise TypeError(
                f"AnthropicCompatClient.create: unsupported kwargs {sorted(extra)}")
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
        usage = _openai_usage(resp)
        _log_usage(kwargs["model"], usage, stop_reason == "max_tokens", max_tokens)
        return _Message(choice.message.content or "", stop_reason, usage)


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


def llm_client_env_key() -> str:
    """Name of the env var the active LLM_CLIENT_BACKEND needs."""
    return {"anthropic": "ANTHROPIC_API_KEY",
            "openrouter": "OPENROUTER_API_KEY"}[config.LLM_CLIENT_BACKEND]
