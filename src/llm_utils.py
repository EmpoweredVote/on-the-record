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

from . import config
from .event_kinds import speaker_id_framing
from .models import Segment, SpeakerMapping
from .name_matching import normalize as _norm, significant_tokens as _significant_tokens


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
