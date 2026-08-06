"""Shared 'current vs candidate OpenRouter model' client construction for the
summary-model eval scripts (scripts/eval_summary_classify.py,
scripts/generate_summary_ab.py)."""
from __future__ import annotations

import os

from . import config
from .llm_providers import AnthropicCompatClient, make_llm_client


def build_eval_client(model_key: str):
    """Returns (client, model_override).

    "current" -> make_llm_client() (config.LLM_CLIENT_BACKEND's live backend,
    today OpenRouter) with model_override=None, so summarize.py call sites
    fall back to their own config model constants exactly like production.
    Anything else is treated as an OpenRouter model id string and passed
    straight through AnthropicCompatClient (an id already on OpenRouter, e.g.
    "deepseek/deepseek-chat-v3.1", passes through
    llm_providers._OPENROUTER_MODEL_MAP unchanged).

    Raises RuntimeError if OPENROUTER_API_KEY isn't set for a candidate model.
    """
    if model_key == "current":
        return make_llm_client(), None
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(f"{model_key}: OPENROUTER_API_KEY is not set")
    return AnthropicCompatClient(config._OPENROUTER_URL, key), model_key
