"""Score name candidates against human_review gold labels (pure).

Attribution and spelling are kept apart: "misspelled" = right speaker and
first name, different surname spelling (fixed by lookup in slice 2), and it
does not count against the attribution pre-fill bar.
"""
from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Optional

from .models import Segment
from .name_candidates import Candidate
from .name_matching import normalize, significant_tokens
from .speaker_id_eval import classify

PREFILL_MIN_PRECISION = 0.95
PREFILL_MAX_BAD = 0.02
MISSPELL_MIN_SURNAME_SIMILARITY = 0.4
OUTCOMES = ("correct", "misspelled", "wrong", "hallucination", "miss", "safe_null")
_GOLD_JUNK = re.compile(
    r"^\s*speaker[_ ]?\d+$|^\s*candidate\s*\d+$|\b(unknown|unidentified)\b|\(.*?(unknown|unidentified).*?\)",
    re.I
)


def gold_labels(meeting: dict) -> dict[str, Optional[str]]:
    """Extract label→gold-name from human_review segments (first usable name per label)."""
    gold: dict[str, Optional[str]] = {}
    found: set[str] = set()  # Labels with usable names found
    for s in meeting.get("segments", []):
        if s.get("id_method") != "human_review" or not s.get("speaker_label"):
            continue
        label = s["speaker_label"]
        if label in found:  # Already found a usable name for this label
            continue
        name = s.get("speaker_name")
        if not name or _GOLD_JUNK.search(name):
            # Junk name; mark as None if not yet seen, but continue looking for a usable name
            if label not in gold:
                gold[label] = None
        else:
            # Usable name; use it and mark as found (won't be overwritten)
            gold[label] = name
            found.add(label)
    return gold


def strip_names(meeting: dict) -> list[Segment]:
    segs = [Segment.from_dict(s) for s in meeting.get("segments", [])]
    for s in segs:
        s.speaker_name = None
        s.id_method = None
        s.confidence = None
    return segs


def score_meeting(gold: dict[str, Optional[str]], candidates: dict[str, Candidate],
                  event_kind: Optional[str]) -> list[dict]:
    rows = []
    for label, gold_name in gold.items():
        cand = candidates.get(label)
        predicted = cand.prefill_name if cand else None
        outcome = classify(gold_name, predicted)
        # Promote "wrong" to "misspelled" only if:
        # - Both names have ≥2 significant tokens
        # - First significant tokens match
        # - Surnames share their first letter (ASR keeps the first sound) and similarity ≥ threshold
        if outcome == "wrong" and gold_name and predicted:
            g_sig = significant_tokens(gold_name)
            p_sig = significant_tokens(predicted)
            if len(g_sig) >= 2 and len(p_sig) >= 2 and g_sig[0] == p_sig[0]:
                g_surname = g_sig[-1]
                p_surname = p_sig[-1]
                similarity = SequenceMatcher(None, g_surname, p_surname).ratio()
                if g_surname[:1] == p_surname[:1] and similarity >= MISSPELL_MIN_SURNAME_SIMILARITY:
                    outcome = "misspelled"
        if predicted:
            tier = cand.tier
        elif cand and cand.name:
            tier = "hint"
        else:
            tier = "none"
        rows.append({
            "label": label, "gold": gold_name, "predicted": predicted,
            "hint": cand.name if cand and not predicted else None, "tier": tier,
            "outcome": outcome,
            "exact": bool(outcome == "correct" and normalize(gold_name or "") == normalize(predicted or "")),
            "conflict": cand.conflict if cand else None, "partial": cand.partial if cand else False,
            "event_kind": event_kind,
        })
    return rows


def summarize(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key))].append(r)
    out = {}
    for name, items in sorted(groups.items()):
        counts = {o: sum(1 for r in items if r["outcome"] == o) for o in OUTCOMES}
        predicted = counts["correct"] + counts["misspelled"] + counts["wrong"] + counts["hallucination"]
        precision = (counts["correct"] + counts["misspelled"]) / predicted if predicted else 0.0
        bad = (counts["wrong"] + counts["hallucination"]) / predicted if predicted else 0.0
        out[name] = {
            "n": len(items), **counts, "predicted": predicted,
            "precision": round(precision, 3), "bad_rate": round(bad, 3),
            "misspell_rate": round(counts["misspelled"] / predicted, 3) if predicted else 0.0,
            "exact_rate": round(sum(1 for r in items if r.get("exact")) / predicted, 3) if predicted else 0.0,
            "passes_prefill_bar": bool(predicted and precision >= PREFILL_MIN_PRECISION
                                       and bad <= PREFILL_MAX_BAD),
        }
    return out
