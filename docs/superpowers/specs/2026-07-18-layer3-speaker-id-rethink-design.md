# Layer-3 speaker-ID rethink — Claude-based, model-swappable identifier

**Date:** 2026-07-18
**Type:** Design (replaces the local-llama Layer-3 speaker identifier)
**Status:** Approved — ready for implementation plan

## Problem

Layer-3 speaker identification (`src/llm_utils.py`, `prompt_for_speaker_id`) runs a
small local llama GGUF model (`Qwen2.5-7B-Instruct`, via `llama-cpp-python`). It was
tuned for city-council transcripts and is a weak proper-noun identifier. The
min_confidence tuning run on House-floor clips
(`docs/superpowers/specs/2026-07-18-min-confidence-tuning-findings.md`) exposed it
emitting hallucinated / phonetically-garbled names: `Mr. Bean`, `perlene`
(Fitzpatrick), `rockus` (Bilirakis), `boast` (Bost), `leood` (LaHood), or just
`"speaker"`.

Congressional Record (floor) runs now skip Layer-3 entirely (PR #93,
`should_run_llm` in `run_local.py`) because CREC is an authoritative speaker oracle
there. But Layer-3 remains the fallback identifier for **council / school-board /
community meetings** and — most importantly — **interviews** (`news_clip`,
`press_conference`, `podcast`), which are a large share of the content. Two failure
modes matter there:

1. **Hardcoded framing.** The prompt says "You are analyzing a city council meeting
   transcript" for every event kind, including interviews.
2. **Unanchored invention.** With `roster=None` (interviews, most runs) the model
   invents surnames from nothing. The safe interview answer — when the interviewer
   is never named on air — is `null`, but the model guesses.

## Decisions (from brainstorming)

- **Replace the local llama with a Claude-based identifier** for all non-floor event
  kinds (interviews *and* civic meetings). The local model is the proven weak point;
  the `anthropic` SDK + `ANTHROPIC_API_KEY` are already dependencies (used by
  `src/summarize.py`).
- **Floor runs still skip Layer-3** (`should_run_llm` unchanged — CREC authoritative).
- The model must be **swappable and empirically comparable**. The user wants to test
  whether the cheapest option (Haiku) is good enough, and to evaluate non-Anthropic
  options (Gemini Flash, Kimi/Moonshot, GLM/Zhipu, Deepseek). Build a provider seam
  with an **Anthropic** provider and an **OpenAI-compatible** provider now, plus an
  **eval harness** so the production model is chosen from data, not guessed.
- Default production model: **`haiku`**, pending the eval result.
- ASR/auto-caption garbling of surnames upstream is a **separate concern**, noted but
  not fixed here.

## Architecture

Four units, each independently testable:

```
config.py (registry)  ->  llm_providers.py (provider seam)  ->  llm_utils.py (ID logic)
                                                                      ^
                                              scripts/eval_speaker_id.py (harness)
```

### 1. Provider seam — new `src/llm_providers.py`

A thin abstraction whose only job is "prompt in, text out". Prompt-building,
guardrail, and parsing stay out of it.

```python
class SpeakerIDProvider(Protocol):
    name: str
    def complete(self, prompt: str, *, max_tokens: int, temperature: float) -> str: ...

class AnthropicProvider:      # wraps anthropic.Anthropic() Messages API
class OpenAICompatProvider:   # wraps openai.OpenAI(base_url=..., api_key=...) chat.completions

def get_provider(name: str) -> SpeakerIDProvider:
    """Look up name in config.SPEAKER_ID_MODELS, construct the right provider.
    Raise a clear error if the provider's api_key_env is unset."""
```

`OpenAICompatProvider` covers Gemini Flash, Kimi/Moonshot, GLM/Zhipu, and Deepseek —
all expose OpenAI-compatible chat endpoints — via `base_url` + `api_key_env`.

- **What it does:** turns a prompt string into a completion string for a named model.
- **How you use it:** `get_provider("haiku").complete(prompt, max_tokens=150, temperature=0.0)`.
- **Depends on:** `anthropic` (present), `openai` (new dep), `config.SPEAKER_ID_MODELS`.

### 2. Config registry — `src/config.py`

```python
# --- Layer-3 speaker identification (LLM) ---
SPEAKER_ID_ACTIVE = "haiku"   # production model; the eval decides the final value
SPEAKER_ID_MODELS = {
    "haiku":        {"provider": "anthropic",     "model": "claude-haiku-4-5-20251001"},
    "sonnet":       {"provider": "anthropic",     "model": "claude-sonnet-4-5"},
    "gemini-flash": {"provider": "openai_compat", "model": "<fill>",
                     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                     "api_key_env": "GEMINI_API_KEY"},
    "deepseek":     {"provider": "openai_compat", "model": "deepseek-chat",
                     "base_url": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"},
    "kimi":         {"provider": "openai_compat", "model": "<fill>",
                     "base_url": "https://api.moonshot.ai/v1", "api_key_env": "MOONSHOT_API_KEY"},
    "glm":          {"provider": "openai_compat", "model": "<fill>",
                     "base_url": "<fill>", "api_key_env": "ZHIPU_API_KEY"},
}
```

Production reads `SPEAKER_ID_MODELS[SPEAKER_ID_ACTIVE]`. The default `haiku` needs only
the already-present `ANTHROPIC_API_KEY`. Exact model IDs / base_urls for the
OpenAI-compatible entries are filled in during implementation (verified against each
provider's current docs). Entries whose `api_key_env` is unset are simply unavailable
(the eval skips them and says so).

### 3. Event-kind-aware prompt + guardrail — rewrite `src/llm_utils.py`

Drop the llama loader (`load_llm`/`unload_llm`, `llama_cpp` import). Keep the
orchestration (`llm_identify_speakers`, partial-results save, per-label loop that
feeds identified names back as context) and the JSON parse, but:

**(a) Take `event_kind` and select framing.** Replace the hardcoded
"city council meeting" line with framing from a new
`event_kinds.speaker_id_framing(event_kind)`:

- `council` / `school_board` / `community_meeting`: local-government body —
  elected officials, staff, members of the public giving comment.
- `news_clip` / `press_conference` / `podcast` (interviews): a host/interviewer with
  one or more guests. **Explicitly instruct: the host is frequently never named on
  air — if you cannot determine a speaker's real name from the transcript, answer
  null rather than guess.**
- `debate` / `forum`: candidates and a moderator, usually named near the start.
- `other` / `None`: generic fallback.

**(b) Call the provider** instead of the local `llm(...)`:
`provider.complete(prompt, max_tokens=150, temperature=0.0)`.

**(c) Anchoring guardrail** — a returned non-null name must be *anchored* or it is
rejected (→ null):

- **Roster runs** (council/school_board with a roster): anchor = the **roster**.
  Accept only if the returned name fuzzy-matches a roster member. Downstream roster
  correction still repairs ASR spelling. This blocks invention of non-roster people.
- **No-roster runs** (interviews): anchor = the **transcript**. Accept only if the
  returned surname appears in the *full* transcript text — normalized substring
  match, with a **tight** `difflib` ratio fallback (≈ ≥ 0.85) for minor spelling
  drift. Phonetic garbage ("Mr. Bean", "perlene") is not in the transcript → rejected
  → null, which is the correct interview default.

The existing "these names already belong to other, distinct voices — do not reuse
them; answer null if unsure" note stays.

**(d) Unchanged downstream contract:** returned `SpeakerMapping` keeps
`id_method="llm"` and `confidence=0.75`, so the publish gate and `min_confidence`
behavior are unaffected.

Also centralize `INTERVIEW_KINDS` (currently duplicated in `src/summarize.py` and
`src/event_entities.py`) into `src/event_kinds.py` and repoint both call sites — a
small, in-scope cleanup, since the new framing helper needs the same set.

### 4. Eval harness — new `scripts/eval_speaker_id.py`

Makes the model choice data-driven and stays in the repo for future model testing.

- **Ground truth:** interview/podcast meetings under `~/CouncilScribe/meetings` whose
  `transcript_named.json` labels carry `id_method="human_review"`. Current inventory:
  **27 meetings, 61 named-gold labels, 4 confirmed-null labels.** Only `human_review`
  counts as gold; `voice_profile` labels may be included as flagged "silver" (off by
  default). The meeting list is a CLI arg with this set as the default.
- **Procedure per meeting × model:** call the real
  `llm_identify_speakers(provider, segments, {}, event_kind=...)` with **all speakers
  unknown** (empty starting mappings) — approved as a fair, slightly-harder-than-
  production test that exercises the actual shipping path (framing + guardrail + the
  iterative claimed-name context). Temperature 0.
- **Scoring per model** (name match = normalized/fuzzy against gold):
  - correct-name %, safe-null % (gold null → returned null),
  - hallucination rate (gold null → returned a name),
  - miss rate (gold named → returned null),
  - wrong-name rate (gold named → returned a different name),
  - rough latency and, where derivable, cost.
- **Output:** a comparison table (markdown + CSV) written to the scratchpad / a
  results path. Models whose `api_key_env` is unset are skipped with an explicit note
  (no silent gaps).

### 5. Wiring & cleanup — `run_local.py`, `requirements.txt`

- In `run_local.py`, drop `load_llm()`/`unload_llm()`; when `should_run_llm(...)` is
  true, construct `provider = get_provider(config.SPEAKER_ID_ACTIVE)` and pass it (plus
  `meeting.event_kind`) into `llm_identify_speakers`.
- `should_run_llm` unchanged — floor runs and `--skip-llm` still skip.
- `requirements.txt`: remove `llama-cpp-python`; add `openai`.

## Data flow

```
run_local (non-floor, not --skip-llm)
  -> get_provider(SPEAKER_ID_ACTIVE)
  -> identify_speakers(..., llm_identify_fn=llm_identify_speakers(provider, event_kind, roster_hint))
       -> for each unresolved label:
            build event-kind-aware prompt (+ known/claimed names, + roster hint)
            provider.complete(prompt)
            parse JSON -> name|null
            anchoring guardrail (roster-anchor OR transcript-anchor) -> name|null
            -> SpeakerMapping(id_method="llm", confidence=0.75) or unresolved
```

## Testing (TDD)

Unit tests with **mocked providers** (no live API):

- `speaker_id_framing(event_kind)` returns the right framing for each kind and the
  fallback for `None`/unknown; interview framing contains the null-if-unnamed
  instruction.
- Guardrail: interview mode rejects a name absent from the transcript (→ null) and
  accepts one present (incl. tight fuzzy); roster mode accepts a roster member and
  rejects an invented non-roster name.
- JSON/null parsing unchanged (valid JSON, `null`, `unknown`, malformed).
- `get_provider` selects the correct class and raises a clear error when
  `api_key_env` is unset.
- Eval-harness **scoring** logic on synthetic (gold, predicted) pairs classifies
  correct / safe-null / hallucination / miss / wrong correctly.

Integration (manual, not CI): run `scripts/eval_speaker_id.py` against the real eval
set with `haiku` and `sonnet` (and any OSS models whose keys are set) to produce the
comparison table that sets `SPEAKER_ID_ACTIVE`.

## Success criteria

- Local llama and `llama-cpp-python` are fully removed from the ID path.
- Non-floor runs identify speakers via the configured Claude/OpenAI-compat model.
- Interview runs return `null` for unnameable speakers instead of inventing names;
  no returned name is unanchored to roster-or-transcript.
- The eval harness runs end-to-end over the gold set and emits per-model scores.
- Floor runs and `--skip-llm` behavior are unchanged.
- Publish / `min_confidence` gate behavior is unchanged (`id_method="llm"`,
  `confidence=0.75`).

## Out of scope

- **ASR / auto-caption garbling** of surnames upstream of the LLM (a real accuracy
  ceiling for interviews with no clean spelling of the name anywhere in the audio).
  Noted as a separate future concern.
- Changing the `min_confidence` gate or the CREC formula (settled: keep 0.5).
- Two-pass / production-seeded eval (approved to test with all speakers unknown).

## Eval results (2026-07-19) — production model confirmed: `haiku`

Ran `scripts/eval_speaker_id.py --models haiku sonnet` over the 27 human-labeled
interview meetings (65 gold labels: 61 named + 4 null), all speakers unknown.
`sonnet` here = `claude-sonnet-4-5` (the registry's current entry); `haiku` =
`claude-haiku-4-5-20251001`.

| model  | correct | safe_null | hallucination | miss | wrong | accuracy | seconds |
|--------|---------|-----------|---------------|------|-------|----------|---------|
| haiku  | 9       | 4/4       | **0**         | 50   | 2     | 0.20     | 214     |
| sonnet | 11      | 3/4       | 1             | 43   | 7     | 0.215    | 421     |

**Decision: keep `SPEAKER_ID_ACTIVE = "haiku"`** (no code change; it was already the
default). Rationale:

- **Hallucination is the safety-critical metric, and haiku scored 0** (sonnet 1). The
  old-llama "Mr. Bean" failure mode is gone — the anchoring guardrail works.
- **Name precision (correct / total names emitted): haiku 9/11 = 82%; sonnet
  11/19 = 58%.** Sonnet ventures more names (fewer misses) but is wrong far more
  often. For a civic-data product where a wrong name is worse than "unidentified,"
  that trade is unfavorable.
- Haiku is ~2× faster and ~5× cheaper. Whole two-model run cost < $1.

**Caveat / future lever:** recall is low for both (miss-dominated) because this is a
raw all-unknown test *and* the transcript-anchor deliberately abstains when a guest's
surname never appears in the transcript. Layer-3 is a fallback (voice profiles +
pattern-matching resolve most speakers first), so the conservative bias is acceptable.
If recall becomes a priority, the next investigation is whether the transcript-anchor
rejects names that *are* spoken (vs. genuinely absent) — a tuning pass, not a model
change. Full per-meeting trace was captured in the eval run log.
