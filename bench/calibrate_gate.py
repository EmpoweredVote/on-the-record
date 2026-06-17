#!/usr/bin/env python3
"""Non-destructive calibration of the meeting confidence gate.

For each reviewed meeting (corrected transcript_named.json = ground truth),
re-derive the AUTOMATED Stage-4 attributions by calling identify_speakers()
directly against the saved diarization/embeddings + current profiles + roster.
Never runs the full pipeline, never enrolls, never writes to the meeting dir.

Reports per-meeting and per-event-kind:
  trusted_coverage  — what the gate would score on the automated output
  trusted_precision — of the speech-time the trusted+probable tiers CLAIMED,
                      how much matched the ground-truth identity (link-first)

Usage:
  .venv/bin/python bench/calibrate_gate.py                 # all reviewed meetings
  .venv/bin/python bench/calibrate_gate.py 2026-02-10-regular-session ...
  .venv/bin/python bench/calibrate_gate.py --with-llm      # include Layer 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import config, quality
from src.models import Meeting, Segment, SpeakerMapping
from src.identify import identify_speakers


def _speech_by_label(segments) -> dict[str, float]:
    secs: dict[str, float] = {}
    for s in segments:
        dur = max(0.0, (s.end_time or 0.0) - (s.start_time or 0.0))
        secs[s.speaker_label] = secs.get(s.speaker_label, 0.0) + dur
    return secs


def _same_identity(auto: SpeakerMapping | None, truth: SpeakerMapping | None) -> bool:
    """True if auto and truth refer to the same person.

    Tolerant of link drift: meetings reviewed before identity-linking existed
    store a bare name with no slug, while a current automated re-run may carry a
    politician_slug for the same person. So: if BOTH sides are linked, require a
    slug match (linked to different people = mismatch); otherwise fall back to
    normalized-name equality. This measures 'did automation pick the right
    person', not 'did it record the same linkage'.
    """
    if auto is None or truth is None:
        return False
    if not auto.speaker_name or not truth.speaker_name:
        return False
    a_slug = auto.politician_slug or auto.local_slug
    t_slug = truth.politician_slug or truth.local_slug
    if a_slug and t_slug:
        return a_slug == t_slug
    return quality._normalize_name(auto.speaker_name) == quality._normalize_name(truth.speaker_name)


def compare(truth: Meeting, auto_mappings: dict[str, SpeakerMapping]) -> dict:
    """Per-label, speech-time-weighted precision of the automated TRUSTED+PROBABLE tiers.

    A label is 'claimed' when its automated tier is trusted or probable. It is
    'correct' when it refers to the same person as truth (see _same_identity,
    which tolerates link drift between pre-linking truth and a linked re-run).
    """
    secs = _speech_by_label(truth.segments)
    claimed = 0.0
    correct = 0.0
    trusted_secs = 0.0
    total = sum(secs.values()) or 0.0

    for label, label_secs in secs.items():
        auto = auto_mappings.get(label)
        tier = quality.classify_method(auto.id_method) if (auto and auto.speaker_name) else quality.TIER_UNKNOWN
        if tier == quality.TIER_TRUSTED:
            trusted_secs += label_secs
        if tier in (quality.TIER_TRUSTED, quality.TIER_PROBABLE):
            claimed += label_secs
            if _same_identity(auto, truth.speakers.get(label)):
                correct += label_secs

    return {
        "trusted_claimed_seconds": round(claimed, 1),
        "trusted_correct_seconds": round(correct, 1),
        "trusted_precision": round(correct / claimed, 4) if claimed else 1.0,
        "trusted_coverage": round(trusted_secs / total, 4) if total else 0.0,
    }


def _rederive_auto(meeting_dir: Path, truth: Meeting, with_llm: bool) -> dict[str, SpeakerMapping]:
    """Run identify_speakers() against saved artifacts. Read-only; never enrolls."""
    from src.enroll import get_stored_centroids, load_profiles

    emb_path = meeting_dir / "embeddings.json"
    if emb_path.exists():
        emb = json.loads(emb_path.read_text())
        embeddings = {k: np.array(v) for k, v in emb.items()}
    else:
        embeddings = {}

    profile_db = load_profiles()
    centroids = get_stored_centroids(profile_db)

    body_slug = None
    state_file = meeting_dir / "pipeline_state.json"
    if state_file.exists():
        body_slug = json.loads(state_file.read_text()).get("body_slug")

    roster = None
    try:
        from src.roster import load_roster
        roster = load_roster(body_slug=body_slug) if body_slug else load_roster()
    except Exception:
        roster = None

    llm_fn = None  # Layer 3 only feeds the unverified tier; skip by default.
    if with_llm:
        from src.llm_utils import llm_identify_speakers, load_llm
        _llm = load_llm()
        llm_fn = lambda segs, maps: llm_identify_speakers(_llm, segs, maps)

    return identify_speakers(
        truth.segments, embeddings,
        stored_profiles=centroids or None,
        llm_identify_fn=llm_fn,
        roster=roster,
        profile_db=profile_db,
    )


def calibrate_meeting(meeting_dir: Path, with_llm: bool) -> dict | None:
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return None
    truth = Meeting.from_dict(json.loads(named.read_text()))
    auto = _rederive_auto(meeting_dir, truth, with_llm)
    result = compare(truth, auto)
    result["meeting_id"] = meeting_dir.name
    result["event_kind"] = truth.event_kind
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Calibrate the confidence gate against reviewed meetings.")
    ap.add_argument("meeting_ids", nargs="*", help="Specific meeting IDs (default: all)")
    ap.add_argument("--with-llm", action="store_true", help="Include Layer-3 LLM in re-derivation")
    args = ap.parse_args()

    if args.meeting_ids:
        dirs = [config.MEETINGS_DIR / m for m in args.meeting_ids]
    else:
        dirs = sorted(d for d in config.MEETINGS_DIR.iterdir()
                      if d.is_dir() and not d.name.startswith("."))

    rows = []
    for d in dirs:
        res = calibrate_meeting(d, args.with_llm)
        if res:
            rows.append(res)

    if not rows:
        print("No reviewed meetings (transcript_named.json) found to calibrate against.")
        return

    print(f"{'meeting':<34} {'kind':<16} {'cov':>6} {'prec':>6}")
    print("-" * 66)
    for r in rows:
        print(f"{r['meeting_id']:<34} {r['event_kind']:<16} "
              f"{r['trusted_coverage']:>6.0%} {r['trusted_precision']:>6.0%}")

    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        by_kind.setdefault(r["event_kind"], []).append(r)

    print("\nPer-event-kind (set HIGH where precision ~= 100%):")
    for kind, krows in sorted(by_kind.items()):
        covs = [r["trusted_coverage"] for r in krows]
        precs = [r["trusted_precision"] for r in krows]
        print(f"  {kind:<16} n={len(krows)}  "
              f"min_cov={min(covs):.0%}  mean_prec={sum(precs)/len(precs):.0%}  "
              f"min_prec={min(precs):.0%}")


if __name__ == "__main__":
    main()
