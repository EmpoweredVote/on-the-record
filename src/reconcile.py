# src/reconcile.py
"""Transcript-as-corrector.

When a source publishes a clean transcript (e.g. an NPR/Brightspot article
page), we still run Whisper + diarization for timestamps and speaker turns, then
use the clean transcript as an LLM reference to fix proper nouns, mishearings,
and punctuation — WITHOUT changing any timing or speaker attribution. If the
reference and the Whisper output don't overlap enough, reconciliation is skipped
so a mismatched reference can never corrupt the segments.

Pure and injectable: pass a `call_llm(prompt) -> str` so tests need no network.
"""
from __future__ import annotations

import json
import re

_WORD_RE = re.compile(r"[a-z0-9']+")
_CHUNK_SEGMENTS = 40  # segments per LLM call, to bound prompt size


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def word_overlap_ratio(a: str, b: str) -> float:
    """Jaccard overlap of the word sets of a and b (0.0-1.0)."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _build_prompt(chunk, reference: str) -> str:
    lines = [f"{s.segment_id}: {s.text}" for s in chunk]
    return (
        "You are correcting an automatic (Whisper) transcript using a clean "
        "reference transcript of the SAME audio.\n"
        "Fix proper nouns, mishearings, and punctuation ONLY. Do NOT merge, "
        "split, reorder, add, or drop segments. Return STRICT JSON mapping each "
        "segment id (string) to its corrected text.\n\n"
        f"Reference transcript:\n{reference}\n\n"
        f"Whisper segments:\n" + "\n".join(lines) + "\n\n"
        'Return only JSON, e.g. {"0": "Corrected text.", "1": "..."}'
    )


def reconcile_segments(segments, reference_text: str, *, call_llm, min_overlap: float = 0.30):
    """Correct segment text against a reference transcript.

    Returns (segments, applied). `applied` is False (and segments are returned
    unchanged) when there is no reference or the word overlap is below
    min_overlap. Timing and speaker_label are never modified.
    """
    reference = (reference_text or "").strip()
    if not reference or not segments:
        return segments, False

    whisper_text = " ".join(s.text for s in segments)
    if word_overlap_ratio(whisper_text, reference) < min_overlap:
        return segments, False

    by_id = {s.segment_id: s for s in segments}
    for i in range(0, len(segments), _CHUNK_SEGMENTS):
        chunk = segments[i:i + _CHUNK_SEGMENTS]
        try:
            raw = call_llm(_build_prompt(chunk, reference))
            match = re.search(r"\{[\s\S]*\}", raw)
            corrections = json.loads(match.group()) if match else {}
            if not isinstance(corrections, dict):
                corrections = {}
        except Exception:
            corrections = {}
        chunk_ids = {s.segment_id for s in chunk}
        for sid, text in corrections.items():
            try:
                key = int(sid)
            except (TypeError, ValueError):
                continue
            if key not in chunk_ids:
                continue
            seg = by_id.get(key)
            if seg is not None and isinstance(text, str) and text.strip():
                seg.text = text.strip()

    return segments, True
