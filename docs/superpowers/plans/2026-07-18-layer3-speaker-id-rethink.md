# Layer-3 Speaker-ID Rethink Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the local-llama Layer-3 speaker identifier with a model-swappable, event-kind-aware, hallucination-guarded identifier backed by Claude (default) or any OpenAI-compatible model, plus an eval harness that picks the production model from human-labeled data.

**Architecture:** A thin provider seam (`src/llm_providers.py`) turns a prompt into text for a named model (Anthropic SDK or OpenAI-compatible endpoint) selected from a config registry. `src/llm_utils.py` keeps the Layer-3 orchestration but builds an event-kind-aware prompt, calls the provider, and rejects any returned name not anchored to the roster (civic runs) or the transcript (interviews). A pure scoring module (`src/speaker_id_eval.py`) plus a CLI (`scripts/eval_speaker_id.py`) score candidate models against the 27 human-labeled interview meetings.

**Tech Stack:** Python, pytest, `anthropic` (already a dep), `openai` (new dep). Floor (Congressional Record) runs and `--skip-llm` are untouched.

**Spec:** `docs/superpowers/specs/2026-07-18-layer3-speaker-id-rethink-design.md`

---

## File Structure

- **Create** `src/llm_providers.py` — provider protocol + `AnthropicProvider` + `OpenAICompatProvider` + `get_provider()` factory. One job: prompt → text.
- **Create** `src/speaker_id_eval.py` — pure scoring logic (null detection, classify a prediction vs gold, aggregate).
- **Create** `scripts/eval_speaker_id.py` — CLI harness: load labeled meetings, run each model's real ID path all-unknown, print comparison table.
- **Create** `tests/test_llm_providers.py`, `tests/test_speaker_id_eval.py`.
- **Modify** `src/config.py` — add `SPEAKER_ID_ACTIVE` + `SPEAKER_ID_MODELS` registry; existing summary-model constants unchanged.
- **Modify** `src/event_kinds.py` — add `INTERVIEW_KINDS` + `speaker_id_framing()`.
- **Modify** `src/summarize.py`, `src/event_entities.py` — import `INTERVIEW_KINDS` from `event_kinds` (remove local dupes).
- **Modify** `src/llm_utils.py` — drop llama loader; event-kind prompt; provider call; anchoring guardrail.
- **Modify** `run_local.py` — construct a provider instead of loading llama; pass `event_kind`/`roster`.
- **Modify** `requirements.txt` — remove `llama-cpp-python`; add `openai`.
- **Modify** `tests/test_llm_identify.py` — update to the provider interface + add guardrail tests.
- **Modify** `tests/test_event_kinds.py` — import `_INTERVIEW_KINDS` from `event_kinds` (its import line moves).

---

## Task 1: Centralize `INTERVIEW_KINDS` + add `speaker_id_framing()`

**Files:**
- Modify: `src/event_kinds.py`
- Modify: `src/summarize.py:20`
- Modify: `src/event_entities.py:16`
- Modify: `tests/test_event_kinds.py:9`
- Test: `tests/test_event_kinds.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_event_kinds.py`:

```python
from src.event_kinds import INTERVIEW_KINDS, speaker_id_framing


def test_interview_kinds_matches_legacy_set():
    assert INTERVIEW_KINDS == {"news_clip", "press_conference", "podcast"}


def test_framing_interview_mentions_host_and_null():
    framing = speaker_id_framing("podcast")
    assert "host" in framing.lower() or "interview" in framing.lower()


def test_framing_civic_mentions_officials():
    framing = speaker_id_framing("council")
    assert "official" in framing.lower() or "government" in framing.lower()


def test_framing_debate_mentions_candidates():
    assert "candidate" in speaker_id_framing("debate").lower()


def test_framing_none_is_generic_nonempty():
    assert speaker_id_framing(None).strip() != ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_event_kinds.py -k "framing or interview_kinds" -v`
Expected: FAIL with `ImportError: cannot import name 'INTERVIEW_KINDS'`

- [ ] **Step 3: Add `INTERVIEW_KINDS` + framing to `src/event_kinds.py`**

Append to `src/event_kinds.py` (after the existing `resolve_local_role` function):

```python
# --- Interview-style event kinds (host + guest formats) --------------------
# Single source of truth; imported by summarize.py and event_entities.py.
INTERVIEW_KINDS = frozenset({"news_clip", "press_conference", "podcast"})


# --- Layer-3 speaker-ID prompt framing per event kind ----------------------
_CIVIC_FRAMING = (
    "You are analyzing a transcript of a local government meeting (city council, "
    "school board, or community meeting). Speakers are typically elected officials, "
    "government staff, and members of the public giving comment."
)
_INTERVIEW_FRAMING = (
    "You are analyzing an interview or podcast transcript. Typically a host or "
    "interviewer speaks with one or more guests. The host or interviewer is "
    "frequently never named on air."
)
_DEBATE_FRAMING = (
    "You are analyzing a candidate debate or forum transcript. Speakers are "
    "candidates and a moderator, who are usually named near the beginning."
)
_DEFAULT_FRAMING = "You are analyzing a meeting transcript."


def speaker_id_framing(event_kind) -> str:
    """One- or two-sentence framing for the Layer-3 speaker-ID prompt."""
    if event_kind in INTERVIEW_KINDS:
        return _INTERVIEW_FRAMING
    if event_kind in ("council", "school_board", "community_meeting"):
        return _CIVIC_FRAMING
    if event_kind in ("debate", "forum"):
        return _DEBATE_FRAMING
    return _DEFAULT_FRAMING
```

- [ ] **Step 4: Repoint the two duplicate definitions**

In `src/summarize.py`, replace line 20 (`_INTERVIEW_KINDS = {"news_clip", "press_conference", "podcast"}`) with:

```python
from .event_kinds import INTERVIEW_KINDS as _INTERVIEW_KINDS
```

In `src/event_entities.py`, replace line 16 (`_INTERVIEW_KINDS = {"news_clip", "press_conference", "podcast"}`) with:

```python
from .event_kinds import INTERVIEW_KINDS as _INTERVIEW_KINDS
```

In `tests/test_event_kinds.py`, change line 9 (`from src.summarize import _INTERVIEW_KINDS`) to:

```python
from src.event_kinds import INTERVIEW_KINDS as _INTERVIEW_KINDS
```

- [ ] **Step 5: Run the affected tests**

Run: `.venv/bin/python -m pytest tests/test_event_kinds.py tests/test_event_entities.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add src/event_kinds.py src/summarize.py src/event_entities.py tests/test_event_kinds.py
git commit -m "refactor: centralize INTERVIEW_KINDS + add speaker_id_framing in event_kinds"
```

---

## Task 2: Config registry + provider seam (`src/llm_providers.py`)

**Files:**
- Modify: `src/config.py` (after line 51, the summary-model block)
- Create: `src/llm_providers.py`
- Test: `tests/test_llm_providers.py`

- [ ] **Step 1: Add the config registry**

In `src/config.py`, after the `SUMMARY_CHUNK_SIZE` line (line 51), add:

```python
# --- Layer-3 speaker identification (LLM) ---
# Production model key; the eval harness (scripts/eval_speaker_id.py) decides the
# final value. Default "haiku" needs only the already-present ANTHROPIC_API_KEY.
SPEAKER_ID_ACTIVE = "haiku"
SPEAKER_ID_MAX_TOKENS = 150
SPEAKER_ID_MODELS = {
    "haiku":  {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    "sonnet": {"provider": "anthropic", "model": "claude-sonnet-4-5"},
    # OpenAI-compatible endpoints. Model ids / base_urls are placeholders to be
    # verified against each provider's current docs before first use; they are
    # only reachable when their api_key_env is set (the eval skips the rest).
    "gemini-flash": {"provider": "openai_compat", "model": "gemini-2.5-flash",
                     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                     "api_key_env": "GEMINI_API_KEY"},
    "deepseek": {"provider": "openai_compat", "model": "deepseek-chat",
                 "base_url": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"},
    "kimi": {"provider": "openai_compat", "model": "moonshot-v1-8k",
             "base_url": "https://api.moonshot.ai/v1", "api_key_env": "MOONSHOT_API_KEY"},
    "glm": {"provider": "openai_compat", "model": "glm-4-flash",
            "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key_env": "ZHIPU_API_KEY"},
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_llm_providers.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_llm_providers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.llm_providers'`

- [ ] **Step 4: Implement `src/llm_providers.py`**

Create `src/llm_providers.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_llm_providers.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add src/config.py src/llm_providers.py tests/test_llm_providers.py
git commit -m "feat: add Layer-3 model provider seam + config registry"
```

---

## Task 3: Rewrite `src/llm_utils.py` — event-kind prompt, provider call, anchoring guardrail

**Files:**
- Modify: `src/llm_utils.py` (full rewrite of prompt/parse; drop llama loader)
- Modify: `tests/test_llm_identify.py`
- Test: `tests/test_llm_identify.py`

- [ ] **Step 1: Rewrite the existing tests to the provider interface**

Replace the entire body of `tests/test_llm_identify.py` with:

```python
"""Tests for the Layer-3 LLM speaker-identification step (src.llm_utils).

Regression guard: in an interview dominated by one enrolled person, the prompt
must tell the model the known names belong to other voices and permit abstaining
(null). Plus: a returned name must be anchored to the transcript (interviews) or
roster (civic) or it is rejected. See interview-chris-swanson-wdiv.
"""
from __future__ import annotations

from src.llm_utils import prompt_for_speaker_id
from src.models import Segment, SpeakerMapping


class _Provider:
    """Fake provider that records the prompt and returns a fixed JSON string."""

    def __init__(self, response='{"name": null, "reasoning": "cannot tell"}'):
        self.name = "fake"
        self.model = "fake"
        self.prompt = None
        self._response = response

    def complete(self, prompt, *, max_tokens=150, temperature=0.0):
        self.prompt = prompt
        return self._response


def _segments():
    return [
        Segment(segment_id=0, start_time=0.0, end_time=5.0,
                speaker_label="SPEAKER_00", text="So tell me about your campaign, Jane Smith."),
        Segment(segment_id=1, start_time=5.0, end_time=10.0,
                speaker_label="SPEAKER_01", text="Happy to. My focus is public safety."),
    ]


def _claimed():
    return {"SPEAKER_01": SpeakerMapping(
        speaker_label="SPEAKER_01", speaker_name="Chris Swanson", confidence=0.96)}


def test_prompt_marks_known_names_as_claimed_by_other_voices():
    p = _Provider()
    prompt_for_speaker_id(p, _segments(), _claimed(), "SPEAKER_00")
    assert "already" in p.prompt.lower()
    assert "different" in p.prompt.lower() or "another" in p.prompt.lower()
    assert "Chris Swanson" in p.prompt


def test_prompt_permits_abstaining_with_null():
    p = _Provider()
    prompt_for_speaker_id(p, _segments(), _claimed(), "SPEAKER_00")
    assert "null" in p.prompt.lower()


def test_prompt_uses_event_kind_framing():
    p = _Provider()
    prompt_for_speaker_id(p, _segments(), {}, "SPEAKER_00", event_kind="podcast")
    assert "interview" in p.prompt.lower() or "host" in p.prompt.lower()


def test_null_response_maps_to_no_mapping():
    p = _Provider('{"name": null, "reasoning": "cannot tell"}')
    assert prompt_for_speaker_id(p, _segments(), _claimed(), "SPEAKER_00") is None


def test_name_in_transcript_is_accepted():
    p = _Provider('{"name": "Jane Smith", "reasoning": "addressed by name"}')
    result = prompt_for_speaker_id(p, _segments(), {}, "SPEAKER_01")
    assert result is not None
    assert result.speaker_name == "Jane Smith"
    assert result.id_method == "llm"
    assert result.confidence == 0.75


def test_name_absent_from_transcript_is_rejected_as_hallucination():
    # "Mr. Bean" appears nowhere in the transcript -> guardrail returns None.
    p = _Provider('{"name": "Mr. Bean", "reasoning": "guess"}')
    assert prompt_for_speaker_id(p, _segments(), {}, "SPEAKER_01") is None


def test_roster_mode_accepts_roster_member_not_in_window():
    from src.roster import Roster, RosterMember
    roster = Roster(members=[RosterMember(name="President Asare", aliases=[])])
    p = _Provider('{"name": "President Asare", "reasoning": "chairs the meeting"}')
    result = prompt_for_speaker_id(
        p, _segments(), {}, "SPEAKER_00", event_kind="council", roster=roster)
    assert result is not None
    assert result.speaker_name == "President Asare"


def test_roster_mode_rejects_non_roster_name():
    from src.roster import Roster, RosterMember
    roster = Roster(members=[RosterMember(name="President Asare", aliases=[])])
    p = _Provider('{"name": "Councilmember Nonexistent", "reasoning": "guess"}')
    result = prompt_for_speaker_id(
        p, _segments(), {}, "SPEAKER_00", event_kind="council", roster=roster)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_llm_identify.py -v`
Expected: FAIL — old signature (`prompt_for_speaker_id(llm, ...)` returning llama dict) and missing `event_kind`/`roster` kwargs and guardrail.

- [ ] **Step 3: Rewrite `src/llm_utils.py`**

Replace the entire contents of `src/llm_utils.py` with:

```python
"""Layer 3: LLM-assisted speaker identification.

Sends a transcript window around each unresolved speaker to a configured model
(see src/llm_providers.py), then rejects any returned name that is not anchored
to the roster (civic runs) or the transcript (interviews). Floor runs skip this
layer entirely (see run_local.should_run_llm).
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Optional

from .event_kinds import speaker_id_framing
from .models import Segment, SpeakerMapping

_HONORIFICS = {
    "mr", "mrs", "ms", "dr", "rep", "sen", "senator", "representative",
    "president", "chair", "chairman", "chairwoman", "chairperson",
    "councilmember", "council", "member", "mayor", "the", "hon", "honorable",
    "gov", "governor", "speaker",
}


def _norm(text: str) -> str:
    """Lowercase, non-alphanumerics -> spaces, collapse whitespace."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _significant_tokens(name: str) -> list[str]:
    """Name tokens minus honorifics and tokens shorter than 3 chars."""
    return [t for t in _norm(name).split() if len(t) >= 3 and t not in _HONORIFICS]


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _matches_roster(surname: str, roster) -> bool:
    for m in roster.members:
        candidates = _significant_tokens(m.name)
        for alias in (getattr(m, "aliases", None) or []):
            candidates += _significant_tokens(alias)
        for c in candidates:
            if surname == c or _ratio(surname, c) >= 0.85:
                return True
    return False


def _name_is_anchored(name: str, roster, segments: list[Segment]) -> bool:
    """A returned name must be anchored, or it is a hallucination.

    Civic runs with a roster: anchor is the roster (blocks non-roster invention).
    Interviews (no roster): anchor is the transcript (blocks "Mr. Bean").
    """
    tokens = _significant_tokens(name)
    if not tokens:
        return False
    surname = tokens[-1]
    if roster is not None and getattr(roster, "members", None):
        return _matches_roster(surname, roster)
    transcript = " ".join(_norm(s.text) for s in segments if s.text)
    if surname and surname in transcript:
        return True
    return any(_ratio(surname, t) >= 0.85 for t in set(transcript.split()))


def _parse_name(text: str) -> Optional[str]:
    """Extract the candidate name from the model's JSON, or None to abstain."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    name = data.get("name")
    if not name or str(name).strip().lower() in ("null", "unknown", "none"):
        return None
    return str(name).strip()


def prompt_for_speaker_id(
    provider,
    segments: list[Segment],
    current_mappings: dict[str, SpeakerMapping],
    unknown_label: str,
    *,
    event_kind=None,
    window_size: int = 20,
    roster=None,
    roster_hint: str = "",
) -> Optional[SpeakerMapping]:
    """Ask the configured model to identify one unknown speaker from context.

    Returns a SpeakerMapping (id_method='llm', confidence=0.75) only when the
    returned name is anchored to the roster or transcript; otherwise None.
    """
    unknown_indices = [
        i for i, s in enumerate(segments) if s.speaker_label == unknown_label
    ]
    if not unknown_indices:
        return None

    # Center on an occurrence away from the very start (roll call lacks context).
    if len(unknown_indices) >= 3:
        center = unknown_indices[len(unknown_indices) // 3]
    else:
        center = unknown_indices[0]
    start = max(0, center - window_size // 2)
    end = min(len(segments), center + window_size // 2)
    window = segments[start:end]

    lines = []
    for seg in window:
        mapping = current_mappings.get(seg.speaker_label)
        if mapping and mapping.speaker_name:
            speaker = mapping.speaker_name
        elif seg.speaker_label == unknown_label:
            speaker = f"[UNKNOWN - {unknown_label}]"
        else:
            speaker = seg.speaker_label
        lines.append(f"{speaker}: {seg.text}")
    transcript_excerpt = "\n".join(lines)

    known_speakers = [
        f"  - {label} = {m.speaker_name}"
        for label, m in current_mappings.items()
        if m.speaker_name
    ]
    known_section = "\n".join(known_speakers) if known_speakers else "  (none identified yet)"

    claimed_note = ""
    if any(m.speaker_name for m in current_mappings.values()):
        claimed_note = (
            "\nThe names under 'Known speakers' already belong to different "
            "voices. Do NOT reuse any of them for this speaker — it is another, "
            "distinct person. If you cannot identify who this is from the "
            "context, answer with null rather than guessing a name that is "
            "already taken or merely discussed in the transcript.\n"
        )

    roster_section = ""
    if roster_hint:
        roster_section = (
            f"\n{roster_hint}\nIMPORTANT: Use the exact names from this roster when "
            "identifying speakers. Transcription may misspell names.\n"
        )

    prompt = f"""{speaker_id_framing(event_kind)} Your job is to identify one speaker.
{roster_section}
Known speakers:
{known_section}
{claimed_note}
Unknown speaker to identify: {unknown_label}

Transcript excerpt:
---
{transcript_excerpt}
---

Based on the context, who is {unknown_label}? Consider:
- How other speakers address them
- What topics they discuss and their role
- Conversational patterns and turn-taking

If the transcript does not contain enough information to name this speaker,
answer with null rather than guessing.

Respond with ONLY a JSON object:
{{"name": "Speaker Name or null", "reasoning": "brief explanation"}}"""

    from . import config

    text = provider.complete(
        prompt,
        max_tokens=config.SPEAKER_ID_MAX_TOKENS,
        temperature=0.0,
    )
    if text and "{" in text and "}" not in text:
        text += "}"

    candidate = _parse_name(text)
    if not candidate:
        return None
    if not _name_is_anchored(candidate, roster, segments):
        return None
    return SpeakerMapping(
        speaker_label=unknown_label,
        speaker_name=candidate,
        confidence=0.75,
        id_method="llm",
    )


def llm_identify_speakers(
    provider,
    segments: list[Segment],
    current_mappings: dict[str, SpeakerMapping],
    *,
    event_kind=None,
    roster=None,
    roster_hint: str = "",
    partial_results_path=None,
) -> dict[str, SpeakerMapping]:
    """Identify all unresolved speakers using the configured model.

    Passed as llm_identify_fn to identify.identify_speakers(). Saves partial
    results after each speaker when partial_results_path is given.
    """
    all_labels = sorted({seg.speaker_label for seg in segments})
    unresolved = [
        label for label in all_labels
        if label not in current_mappings or not current_mappings[label].speaker_name
    ]

    results: dict[str, SpeakerMapping] = {}
    already_done: set[str] = set()
    if partial_results_path:
        try:
            with open(partial_results_path, "r") as f:
                partial = json.load(f)
            for label, data in partial.items():
                results[label] = SpeakerMapping(
                    speaker_label=label,
                    speaker_name=data.get("speaker_name"),
                    confidence=data.get("confidence", 0.75),
                    id_method="llm",
                )
                already_done.add(label)
            if already_done:
                print(f"    Loaded {len(already_done)} partial LLM results from previous run")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    remaining = [l for l in unresolved if l not in already_done]
    total = len(remaining)
    print(f"    LLM identifying {total} unresolved speaker(s)...")

    for i, label in enumerate(remaining):
        print(f"    [{i+1}/{total}] Analyzing {label}...", end=" ", flush=True)
        try:
            mapping = prompt_for_speaker_id(
                provider, segments, current_mappings, label,
                event_kind=event_kind, roster=roster, roster_hint=roster_hint,
            )
            if mapping:
                results[label] = mapping
                current_mappings[label] = mapping
                print(f"-> {mapping.speaker_name}")
            else:
                print("-> (unresolved)")
        except Exception as e:
            print(f"-> error: {e}")
            if partial_results_path:
                _save_partial_results(results, partial_results_path)
                print(f"    Partial results saved ({len(results)} speakers). Re-run to continue.")
            raise

        if partial_results_path:
            _save_partial_results(results, partial_results_path)

    return results


def _save_partial_results(results: dict[str, SpeakerMapping], path) -> None:
    data = {
        label: {
            "speaker_name": m.speaker_name,
            "confidence": m.confidence,
            "id_method": m.id_method,
        }
        for label, m in results.items()
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_llm_identify.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the LLM-skip regression suite (must stay green)**

Run: `.venv/bin/python -m pytest tests/test_llm_skip.py tests/test_identification.py -v`
Expected: PASS (these exercise `should_run_llm` / identify wiring; no llama import should break them)

- [ ] **Step 6: Commit**

```bash
git add src/llm_utils.py tests/test_llm_identify.py
git commit -m "feat: event-kind-aware prompt + anchoring guardrail for Layer-3 speaker ID"
```

---

## Task 4: Wire `run_local.py` + dependency swap

**Files:**
- Modify: `run_local.py:1390-1428`
- Modify: `requirements.txt:12` (remove llama), add `openai`
- Test: `tests/test_llm_skip.py` (existing — must still pass)

- [ ] **Step 1: Replace the llama load/unload wiring**

In `run_local.py`, replace lines 1390-1404 (the `# Layer 3: LLM ...` block through the `elif crec_request ...` print) with:

```python
        # Layer 3: LLM (optional; skipped on Congressional Record runs)
        llm_fn = None
        if should_run_llm(args.skip_llm, crec_request):
            from src.llm_providers import get_provider
            from src.llm_utils import llm_identify_speakers

            provider = get_provider(config.SPEAKER_ID_ACTIVE)
            print(f"  LLM speaker ID via {config.SPEAKER_ID_ACTIVE} ({provider.model})")
            _llm_event_kind = state.event_kind
            llm_fn = lambda segs, maps: llm_identify_speakers(
                provider, segs, maps,
                event_kind=_llm_event_kind, roster=roster, roster_hint=roster_hint,
                partial_results_path=llm_partial_path,
            )
        elif crec_request and not args.skip_llm:
            print("  Skipping LLM speaker ID (Congressional Record run — CREC is "
                  "authoritative; unresolved speakers go to review).")
```

- [ ] **Step 2: Remove the llama unload block**

In `run_local.py`, delete lines 1424-1426 (the now-dangling llama teardown):

```python
        if llm is not None:
            unload_llm(llm)
            del llm
```

Leave the `if llm_partial_path.exists(): llm_partial_path.unlink()` line that follows it intact.

- [ ] **Step 3: Swap the dependency**

In `requirements.txt`, delete the line `llama-cpp-python>=0.3.0` and add:

```
openai>=1.40.0
```

- [ ] **Step 4: Verify no remaining llama references**

Run: `grep -rn "llama_cpp\|load_llm\|unload_llm\|llama-cpp" src/ run_local.py requirements.txt`
Expected: no output (empty)

- [ ] **Step 5: Run the skip/gate suites + import-check run_local**

Run: `.venv/bin/python -c "import run_local" && .venv/bin/python -m pytest tests/test_llm_skip.py tests/test_gate_pipeline.py -v`
Expected: `import run_local` succeeds; tests PASS

- [ ] **Step 6: Commit**

```bash
git add run_local.py requirements.txt
git commit -m "feat: wire Claude provider into Layer-3; drop llama-cpp, add openai"
```

---

## Task 5: Eval harness — `src/speaker_id_eval.py` (scoring) + `scripts/eval_speaker_id.py` (CLI)

**Files:**
- Create: `src/speaker_id_eval.py`
- Create: `scripts/eval_speaker_id.py`
- Test: `tests/test_speaker_id_eval.py`

- [ ] **Step 1: Write the failing scoring tests**

Create `tests/test_speaker_id_eval.py`:

```python
"""Tests for Layer-3 eval scoring (src.speaker_id_eval)."""
from __future__ import annotations

from src.speaker_id_eval import classify, is_null_gold, summarize


def test_is_null_gold():
    assert is_null_gold(None) is True
    assert is_null_gold("") is True
    assert is_null_gold("Unidentified Speaker") is True
    assert is_null_gold("Jane Smith") is False


def test_classify_correct_name():
    assert classify("Jeff Merkley", "Jeff Merkley") == "correct"
    assert classify("Jeff Merkley", "Senator Merkley") == "correct"  # surname match


def test_classify_wrong_name():
    assert classify("Jeff Merkley", "Jane Smith") == "wrong"


def test_classify_safe_null():
    assert classify("Unidentified Speaker", None) == "safe_null"


def test_classify_hallucination():
    assert classify("Unidentified Speaker", "Mr. Bean") == "hallucination"


def test_classify_miss():
    assert classify("Jeff Merkley", None) == "miss"


def test_summarize_counts_and_rates():
    outcomes = ["correct", "correct", "miss", "safe_null", "hallucination", "wrong"]
    row = summarize("haiku", outcomes)
    assert row["model"] == "haiku"
    assert row["n"] == 6
    assert row["correct"] == 2
    assert row["hallucination"] == 1
    assert 0.0 <= row["accuracy"] <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_speaker_id_eval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.speaker_id_eval'`

- [ ] **Step 3: Implement `src/speaker_id_eval.py`**

Create `src/speaker_id_eval.py`:

```python
"""Pure scoring logic for the Layer-3 speaker-ID eval harness.

Kept separate from the CLI (scripts/eval_speaker_id.py) so it is unit-testable
without touching the filesystem or any model API.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional

_HONORIFICS = {
    "mr", "mrs", "ms", "dr", "rep", "sen", "senator", "representative",
    "president", "chair", "chairman", "chairwoman", "councilmember", "mayor",
    "the", "hon", "gov", "governor", "speaker",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _surname(name: str) -> str:
    toks = [t for t in _norm(name).split() if len(t) >= 3 and t not in _HONORIFICS]
    return toks[-1] if toks else ""


def is_null_gold(name: Optional[str]) -> bool:
    """True when the gold/predicted label means 'no real name'."""
    if not name or not str(name).strip():
        return True
    return "unidentified" in str(name).lower()


def _name_match(gold: str, pred: str, fuzzy: float = 0.85) -> bool:
    g, p = _surname(gold), _surname(pred)
    if not g or not p:
        return False
    return g == p or difflib.SequenceMatcher(None, g, p).ratio() >= fuzzy


def classify(gold_name: Optional[str], predicted_name: Optional[str]) -> str:
    """One of: correct | safe_null | hallucination | miss | wrong."""
    gold_null = is_null_gold(gold_name)
    pred_null = is_null_gold(predicted_name)
    if gold_null and pred_null:
        return "safe_null"
    if gold_null and not pred_null:
        return "hallucination"
    if not gold_null and pred_null:
        return "miss"
    return "correct" if _name_match(gold_name, predicted_name) else "wrong"


def summarize(model: str, outcomes: list[str]) -> dict:
    """Aggregate outcome labels into counts + accuracy for one model.

    accuracy = (correct + safe_null) / n : credit for both right names and
    correctly abstaining.
    """
    n = len(outcomes)
    counts = {k: outcomes.count(k) for k in
              ("correct", "safe_null", "hallucination", "miss", "wrong")}
    accuracy = (counts["correct"] + counts["safe_null"]) / n if n else 0.0
    return {"model": model, "n": n, "accuracy": round(accuracy, 3), **counts}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_speaker_id_eval.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Implement the CLI harness `scripts/eval_speaker_id.py`**

Create `scripts/eval_speaker_id.py`:

```python
#!/usr/bin/env python
"""Compare Layer-3 speaker-ID models against human-labeled interview meetings.

For each meeting and each model, run the REAL identification path with all
speakers unknown, then score predictions against the human_review gold labels.

Usage:
  .venv/bin/python scripts/eval_speaker_id.py --models haiku sonnet
  .venv/bin/python scripts/eval_speaker_id.py --meetings-dir ~/CouncilScribe/meetings
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.event_kinds import INTERVIEW_KINDS
from src.llm_providers import get_provider
from src.llm_utils import llm_identify_speakers
from src.models import Segment
from src.speaker_id_eval import classify, summarize


def _gold_labels(meeting_json: dict) -> dict[str, str]:
    """label -> gold name, for human_review labels only (deduped by first seen)."""
    gold: dict[str, str] = {}
    for s in meeting_json.get("segments", []):
        if s.get("id_method") == "human_review":
            gold.setdefault(s["speaker_label"], s.get("speaker_name"))
    return gold


def _segments(meeting_json: dict) -> list[Segment]:
    return [Segment.from_dict(s) for s in meeting_json.get("segments", [])]


def _load_meetings(meetings_dir: Path) -> list[tuple[str, dict]]:
    out = []
    for path in sorted(glob.glob(str(meetings_dir / "*" / "transcript_named.json"))):
        try:
            data = json.load(open(path))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("event_kind") not in INTERVIEW_KINDS:
            continue
        if not _gold_labels(data):
            continue
        out.append((os.path.basename(os.path.dirname(path)), data))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["haiku", "sonnet"],
                    help="keys from config.SPEAKER_ID_MODELS")
    ap.add_argument("--meetings-dir", default=os.path.expanduser("~/CouncilScribe/meetings"))
    args = ap.parse_args()

    meetings = _load_meetings(Path(args.meetings_dir))
    print(f"Loaded {len(meetings)} labeled interview meetings")

    rows = []
    for model in args.models:
        try:
            provider = get_provider(model)
        except (RuntimeError, KeyError) as e:
            print(f"! skipping {model}: {e}")
            continue

        outcomes: list[str] = []
        t0 = time.time()
        for name, data in meetings:
            gold = _gold_labels(data)
            segments = _segments(data)
            preds = llm_identify_speakers(
                provider, segments, {}, event_kind=data.get("event_kind"),
            )
            for label, gold_name in gold.items():
                pred = preds.get(label)
                outcomes.append(classify(gold_name, pred.speaker_name if pred else None))
        row = summarize(model, outcomes)
        row["seconds"] = round(time.time() - t0, 1)
        rows.append(row)
        print(f"  {model}: {row}")

    if not rows:
        print("No models ran (missing API keys?).")
        return

    cols = ["model", "n", "accuracy", "correct", "safe_null", "hallucination", "miss", "wrong", "seconds"]
    print("\n| " + " | ".join(cols) + " |")
    print("|" + "|".join(["---"] * len(cols)) + "|")
    for r in rows:
        print("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Smoke-test the CLI wiring without spending API calls**

Run: `.venv/bin/python scripts/eval_speaker_id.py --models no-such-model`
Expected: prints the loaded-meetings count, then `! skipping no-such-model: 'no-such-model'` and `No models ran`. (Confirms meeting loading + graceful skip without hitting an API.)

- [ ] **Step 7: Commit**

```bash
git add src/speaker_id_eval.py scripts/eval_speaker_id.py tests/test_speaker_id_eval.py
git commit -m "feat: Layer-3 speaker-ID eval harness over human-labeled interviews"
```

---

## Task 6: Full-suite check + eval run to set the production model

**Files:**
- Modify: `src/config.py` (only if the eval says Haiku is not good enough)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (no regressions). If any test imported the removed llama loader or `_INTERVIEW_KINDS` from `summarize`, fix the import and re-run.

- [ ] **Step 2: Run the eval to compare models (requires API keys)**

Run: `.venv/bin/python scripts/eval_speaker_id.py --models haiku sonnet`
Expected: a comparison table with per-model accuracy / safe_null / hallucination / miss / wrong. (Add `gemini-flash deepseek kimi glm` if their key envs are set.)

- [ ] **Step 3: Set the production model from the result**

If Haiku's accuracy and (crucially) hallucination rate are acceptable, leave `SPEAKER_ID_ACTIVE = "haiku"`. Otherwise set it to the best performer, e.g. in `src/config.py`:

```python
SPEAKER_ID_ACTIVE = "sonnet"   # per eval on 2026-07-18: <paste the numbers>
```

- [ ] **Step 4: Record the findings**

Append a short results section (the printed table + the chosen model and why) to `docs/superpowers/specs/2026-07-18-layer3-speaker-id-rethink-design.md`.

- [ ] **Step 5: Commit**

```bash
git add src/config.py docs/superpowers/specs/2026-07-18-layer3-speaker-id-rethink-design.md
git commit -m "chore: set Layer-3 production model from eval results"
```

---

## Self-Review Notes

- **Spec coverage:** provider seam (Task 2) ✓; config registry (Task 2) ✓; event-kind prompt #1 (Tasks 1+3) ✓; anchoring guardrail #2 (Task 3) ✓; eval harness (Task 5) ✓; wiring + llama removal + openai dep (Task 4) ✓; unchanged floor/skip/gate + `id_method="llm"`/`confidence=0.75` (Tasks 3+4) ✓; ASR concern out of scope (spec only) ✓.
- **Type consistency:** `get_provider(name)`, `provider.complete(prompt, *, max_tokens, temperature)`, `prompt_for_speaker_id(provider, segments, current_mappings, unknown_label, *, event_kind, window_size, roster, roster_hint)`, `llm_identify_speakers(provider, segments, current_mappings, *, event_kind, roster, roster_hint, partial_results_path)`, `classify(gold, pred)`, `summarize(model, outcomes)` — used identically across Tasks 2–5.
- **Verified before writing:** `Segment.from_dict` (`src/models.py:53`) and `from src.roster import Roster, RosterMember` both exist; the harness's `Segment.from_dict(s)` mirrors the `run_local.py` loader pattern. `state.event_kind` is populated by checkpoint load / CLI args before the LLM block, so it is the correct event-kind source at the wiring point (the `Meeting` object is not built until after `identify_speakers`).
