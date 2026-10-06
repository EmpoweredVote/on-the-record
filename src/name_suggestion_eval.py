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
PREFILL_MIN_N = 50   # fewer predictions than this cannot establish the bar
ROLE_GOLD = "__ROLE__"  # gold label is a role/junk label: cannot verify a name either way
MISSPELL_MIN_SURNAME_SIMILARITY = 0.4
OUTCOMES = ("correct", "misspelled", "wrong", "hallucination", "unverifiable", "miss", "safe_null")
# Explicitly unnamed by the reviewer: a prediction here is a real hallucination.
_GOLD_UNNAMED = re.compile(r"\b(unknown|unidentified)\b|\(.*?(unknown|unidentified).*?\)", re.I)
# Role-only / placeholder labels: no name to verify against.
_ROLE_WORDS = "moderator|reporter|interviewee|host|panelist|narrator|announcer"
_GOLD_ROLE = re.compile(
    rf"^\s*speaker[_ ]?\d+$|^\s*candidate\s*\d+$|\(\s*candidate\s*\d+\s*\)"
    rf"|^\s*(?:{_ROLE_WORDS})\s*(?:[\d(\-].*)?$", re.I)


def gold_labels(meeting: dict) -> dict[str, Optional[str]]:
    """Extract label→gold-name from human_review segments (first usable name per label).

    Role/placeholder labels map to ROLE_GOLD; explicitly unidentified ones to None."""
    gold: dict[str, Optional[str]] = {}
    found: set[str] = set()  # Labels with usable names found
    for s in meeting.get("segments", []):
        if s.get("id_method") != "human_review" or not s.get("speaker_label"):
            continue
        label = s["speaker_label"]
        if label in found:  # Already found a usable name for this label
            continue
        name = s.get("speaker_name")
        if not name or _GOLD_UNNAMED.search(name) or _GOLD_ROLE.search(name):
            # Junk name; mark as None if not yet seen, but continue looking for a usable name
            if label not in gold:
                gold[label] = ROLE_GOLD if name and not _GOLD_UNNAMED.search(name) else None
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
        if gold_name == ROLE_GOLD:
            outcome = "unverifiable" if predicted else "safe_null"
        else:
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
            "insufficient_n": predicted < PREFILL_MIN_N,
            "strict_precision": round(sum(1 for r in items if r.get("exact")) / predicted, 3) if predicted else 0.0,
            "precision": round(precision, 3), "bad_rate": round(bad, 3),
            "misspell_rate": round(counts["misspelled"] / predicted, 3) if predicted else 0.0,
            "exact_rate": round(sum(1 for r in items if r.get("exact")) / predicted, 3) if predicted else 0.0,
            "passes_prefill_bar": bool(predicted >= PREFILL_MIN_N and precision >= PREFILL_MIN_PRECISION
                                       and bad <= PREFILL_MAX_BAD),
        }
    return out


def score_lookup_rows(rows: list[dict]) -> dict:
    """Spelling outcomes of the web lookup on gold witnesses (slice 2)."""
    from .name_lookup import norm_name

    def exact(a, b):
        return bool(a and b) and norm_name(a) == norm_name(b)

    verified = [r for r in rows if r.get("verified")]
    v_exact = sum(1 for r in verified if exact(r["gold"], r["looked_up"]))
    return {
        "n": len(rows),
        "spoken_exact": sum(1 for r in rows if exact(r["gold"], r["spoken"])),
        "final_exact": sum(1 for r in rows if exact(r["gold"], r["looked_up"] if r.get("verified") else r["spoken"])),
        "verified": len(verified),
        "verified_exact": v_exact,
        "verified_precision": round(v_exact / len(verified), 3) if verified else 0.0,
        "not_found": sum(1 for r in rows if r.get("status") == "not_found"),
        "unavailable": sum(1 for r in rows if r.get("status") == "unavailable"),
        "failed": sum(1 for r in rows if r.get("status") == "failed"),
    }
