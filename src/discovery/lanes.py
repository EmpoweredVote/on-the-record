"""Content lanes for a discovered row, derived from the classifier's
`original_vs_clip` and `event_kind_guess`. Pure — no DB, no I/O — because both
the review UI (row tags) and the auto-approve sweep must agree on the rule.

Note: `src.event_kinds.EVENT_KINDS` has no `interview`/`town_hall`. A full
candidate interview surfaces as `original_vs_clip == 'original'` (the full-event
lane); a town hall is `community_meeting`.
"""
from __future__ import annotations

# Clip OF one of these = a lead to a full primary source worth chasing (lane 2).
FORMAL_EVENT_KINDS = frozenset({
    "debate", "forum", "press_conference", "community_meeting",
})


def content_lane(original_vs_clip: "str | None",
                 event_kind_guess: "str | None") -> str:
    if original_vs_clip == "original":
        return "full_event"
    if original_vs_clip == "clip":
        if event_kind_guess in FORMAL_EVENT_KINDS:
            return "event_clip"
        return "news_clip"
    return "unknown"
