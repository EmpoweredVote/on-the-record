"""Engine B: OpenRouter web-search plugin (one or two chat calls, no tool loop).

SPIKE code -- part of a throwaway bakeoff harness, never imported by the real
repo. Given ONLY a race's level/state/candidates (never ground truth), makes a
single OpenRouter chat.completions call with the "web" plugin enabled
(https://openrouter.ai/docs/guides/features/plugins/web-search), and asks the
model to return the same JSON list of found sources engine_a.py returns. If
the first response isn't clean JSON, a short second "reformat as JSON only"
call is made.

The repo's get_provider()/AnthropicCompatClient abstractions (src/llm_providers.py)
don't expose OpenRouter-only extra_body fields like `plugins`, so this engine
builds its own openai.OpenAI client pointed at the same OpenRouter endpoint +
key the repo already uses (config._OPENROUTER_URL, OPENROUTER_API_KEY) rather
than going through get_provider().
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO_ROOT = Path("/Users/chrisandrews/Documents/GitHub/on-the-record")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import (  # noqa: E402
    FOUND_SOURCE_SHAPE,
    INJECTION_SAFETY_RULES,
    extract_json_list,
    race_context_prompt,
)
import fixtures  # noqa: E402

# Mid-tier OpenRouter model id (same underlying weights as engine_a's
# "haiku-or" provider-map entry: anthropic/claude-haiku-4.5). Constant so
# swapping models is a one-line change.
MODEL_ID = "anthropic/claude-haiku-4.5"
MAX_WEB_RESULTS = 8

SYSTEM_PROMPT = (
    "You are a research assistant hunting for COMPARABLE COMMON-QUESTION sources "
    "for an election race: multi-candidate debates/forums/town-halls, full "
    "candidate interviews, and written questionnaires/voter-guides (Ballotpedia "
    "Candidate Connection, VOTE411/LWV, chamber, local newspaper guides, "
    "government voter pamphlets) -- places where candidates answer the SAME "
    "questions in their own words. Prefer neutral sources; note each source's "
    "ToS bucket; verify it is the CURRENT election cycle (stale prior-cycle pages "
    "are a known trap); note when a page/questionnaire exists but candidates did "
    "not actually answer it (partial fill).\n\n"
    + INJECTION_SAFETY_RULES
    + "\n\n"
    "Use the web search results available to you to find real, current sources. "
    f"Respond with ONLY a JSON array (no prose, no markdown fence). {FOUND_SOURCE_SHAPE}\n"
    "If you find nothing comparable, respond with an empty JSON array []."
)


def _build_user_prompt(race: dict) -> str:
    return (
        race_context_prompt(race)
        + "\nFind comparable common-question sources for this race and respond "
        "with the JSON array described in your instructions."
    )


class _RealClient:
    def __init__(self):
        import openai
        from src import config  # reused per task spec: same OpenRouter endpoint

        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        self._client = openai.OpenAI(base_url=config._OPENROUTER_URL, api_key=key)

    def ask(self, race: dict) -> str:
        resp = self._client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(race)},
            ],
            extra_body={"plugins": [{"id": "web", "max_results": MAX_WEB_RESULTS}]},
        )
        content = resp.choices[0].message.content or ""
        if extract_json_list(content):
            return content

        # Second call: ask for a clean reformat only, per spec's "one or two calls".
        resp2 = self._client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(race)},
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": "Reformat your last answer as ONLY a JSON array, no prose, no fence.",
                },
            ],
        )
        return resp2.choices[0].message.content or ""


class _MockClient:
    def ask(self, race: dict) -> str:
        return fixtures.mock_engine_b_response_text(race["race"])


def run(race: dict, dry_run: bool = False) -> dict:
    """Returns {"status": "ok"|"unavailable", "found": [...], "reason": str|None}."""
    if not dry_run and not os.environ.get("OPENROUTER_API_KEY"):
        return {"status": "unavailable", "reason": "no OPENROUTER_API_KEY", "found": []}

    client = _MockClient() if dry_run else _RealClient()
    content = client.ask(race)
    found = extract_json_list(content)
    return {"status": "ok", "reason": None, "found": found}
