"""Meeting confidence gate: speech-time-weighted trust scoring (Phase A).

Pure functions over a Meeting — no IO. The pipeline computes a verdict after
Stage 4 and routes the meeting to publish / review / failed.
"""
from __future__ import annotations

from typing import Optional

from . import config
from .models import Meeting, SpeakerMapping

# Trust tiers
TIER_TRUSTED = "trusted"
TIER_PROBABLE = "probable"
TIER_UNVERIFIED = "unverified"
TIER_UNKNOWN = "unknown"

# Verdicts
VERDICT_PASS = "pass"
VERDICT_REVIEW = "review"
VERDICT_FAILED = "failed"

_TRUSTED_METHODS = {
    "human_review", "human_confirmed", "voice_profile",
    "roll_call", "self_identification", "chair_recognition",
}
_UNVERIFIED_METHODS = {"llm", "name_addressing", "title_context"}

# Titles stripped when normalizing names for the identity-key fallback.
_TITLES = {
    "councilmember", "councilwoman", "councilman", "alderman", "alderwoman",
    "commissioner", "mayor", "vice-mayor", "president", "vice-president",
    "clerk", "secretary", "treasurer", "supervisor", "representative",
    "chair", "chairman", "chairwoman", "dr", "mr", "mrs", "ms",
}


def classify_method(id_method: Optional[str]) -> str:
    """Map an id_method string to a trust tier."""
    if not id_method:
        return TIER_UNKNOWN
    # Returning-speaker voice matches (lowered threshold) -> probable.
    if id_method.startswith("voice_profile") and "returning_" in id_method:
        return TIER_PROBABLE
    if id_method in _TRUSTED_METHODS:
        return TIER_TRUSTED
    # voice_profile with any other parenthetical tag is still a full match.
    if id_method.startswith("voice_profile"):
        return TIER_TRUSTED
    if id_method in _UNVERIFIED_METHODS:
        return TIER_UNVERIFIED
    return TIER_UNKNOWN


def _normalize_name(name: str) -> str:
    """Lowercase, strip leading titles, collapse whitespace — for name fallback."""
    tokens = [t for t in name.strip().lower().replace(".", "").split() if t]
    filtered = [t for t in tokens if t not in _TITLES]
    return " ".join(filtered or tokens)


def identity_key(mapping: Optional[SpeakerMapping]) -> Optional[str]:
    """Stable identity key for comparison: politician_slug > local_slug > name.

    Returns None for an unidentified speaker (no name and no link).
    """
    if mapping is None:
        return None
    if mapping.politician_slug:
        return f"essentials:{mapping.politician_slug}"
    if mapping.local_slug:
        return f"local:{mapping.local_slug}"
    if mapping.speaker_name:
        return f"name:{_normalize_name(mapping.speaker_name)}"
    return None


def _speech_by_label(meeting: Meeting) -> dict[str, float]:
    secs: dict[str, float] = {}
    for seg in meeting.segments:
        dur = max(0.0, (seg.end_time or 0.0) - (seg.start_time or 0.0))
        secs[seg.speaker_label] = secs.get(seg.speaker_label, 0.0) + dur
    return secs


def _tier_for_label(meeting: Meeting, label: str) -> str:
    m = meeting.speakers.get(label)
    if not m or not m.speaker_name:
        return TIER_UNKNOWN
    return classify_method(m.id_method)


def evaluate_meeting(
    meeting: Meeting,
    *,
    thresholds: Optional[dict] = None,
    discount: Optional[float] = None,
    floor: Optional[float] = None,
) -> dict:
    """Score a meeting and return the quality report dict (written to quality.json)."""
    thresholds = thresholds if thresholds is not None else config.GATE_THRESHOLDS
    discount = config.GATE_PROBABLE_DISCOUNT if discount is None else discount
    floor = config.GATE_SPEECH_FLOOR_SECONDS if floor is None else floor

    secs_by_label = _speech_by_label(meeting)
    total_speech = sum(secs_by_label.values())

    # Eligible (principal) speakers: above the incidental floor. If excluding
    # short speakers would leave none, keep all (avoids div-by-zero on short clips).
    eligible = {l: s for l, s in secs_by_label.items() if s >= floor}
    if not eligible:
        eligible = dict(secs_by_label)
    eligible_total = sum(eligible.values())

    secs_by_tier = {TIER_TRUSTED: 0.0, TIER_PROBABLE: 0.0,
                    TIER_UNVERIFIED: 0.0, TIER_UNKNOWN: 0.0}
    per_speaker = []
    for label in sorted(secs_by_label):
        tier = _tier_for_label(meeting, label)
        secs = secs_by_label[label]
        if label in eligible:
            secs_by_tier[tier] += secs
        m = meeting.speakers.get(label)
        per_speaker.append({
            "label": label,
            "name": (m.speaker_name if m else None),
            "id_method": (m.id_method if m else None),
            "tier": tier,
            "speech_seconds": round(secs, 1),
            "eligible": label in eligible,
        })

    def _cov(tier: str) -> float:
        return (secs_by_tier[tier] / eligible_total) if eligible_total else 0.0

    trusted_coverage = _cov(TIER_TRUSTED)
    probable_coverage = _cov(TIER_PROBABLE)
    unverified_coverage = _cov(TIER_UNVERIFIED)
    unknown_coverage = _cov(TIER_UNKNOWN)
    effective_coverage = (
        (secs_by_tier[TIER_TRUSTED] + discount * secs_by_tier[TIER_PROBABLE])
        / eligible_total
    ) if eligible_total else 0.0

    cfg = thresholds.get(meeting.event_kind) or thresholds["default"]
    if total_speech <= 0:
        verdict = VERDICT_FAILED
        reason = "no speech-time in transcript"
    elif effective_coverage >= cfg["high"]:
        verdict = VERDICT_PASS
        reason = f"effective_coverage {effective_coverage:.2f} >= high {cfg['high']:.2f}"
    elif effective_coverage >= cfg["low"]:
        verdict = VERDICT_REVIEW
        reason = (f"effective_coverage {effective_coverage:.2f} in "
                  f"[{cfg['low']:.2f}, {cfg['high']:.2f})")
    else:
        verdict = VERDICT_FAILED
        reason = f"effective_coverage {effective_coverage:.2f} < low {cfg['low']:.2f}"

    return {
        "verdict": verdict,
        "reason": reason,
        "event_kind": meeting.event_kind,
        "thresholds_used": dict(cfg),
        "trusted_coverage": round(trusted_coverage, 4),
        "probable_coverage": round(probable_coverage, 4),
        "unverified_coverage": round(unverified_coverage, 4),
        "unknown_coverage": round(unknown_coverage, 4),
        "effective_coverage": round(effective_coverage, 4),
        "total_speech_seconds": round(total_speech, 1),
        "eligible_speech_seconds": round(eligible_total, 1),
        "seconds_by_tier": {k: round(v, 1) for k, v in secs_by_tier.items()},
        "per_speaker": per_speaker,
    }
