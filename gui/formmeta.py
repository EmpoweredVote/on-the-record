"""Human-facing labels/help for the new-meeting form. Single source of truth for
the template and the server-side conditional-city guard. Keys must stay in sync
with src.event_kinds.EVENT_KINDS (a test enforces this)."""
from __future__ import annotations

EVENT_KIND_HELP = {
    "council": "City council / board meeting — deliberative, links to a Chamber. Needs a city.",
    "school_board": "School board meeting — deliberative, links to a Chamber. Needs a city.",
    "debate": "Candidates debating within a race — electoral.",
    "forum": "Candidate forum or townhall — electoral.",
    "community_meeting": "A civic or community meeting.",
    "news_clip": "A journalist interviewing a subject.",
    "press_conference": "A subject making a statement and taking questions.",
    "other": "Anything else.",
}

# event kinds that cannot publish without a city (mirrors run_local's guard).
CITY_REQUIRED_KINDS = {"council", "school_board"}

COMPUTE_HELP = {
    "local": "Process on this Mac — no cost, slower for long meetings.",
    "modal": "Process on Modal cloud GPU — free tier, much faster for long meetings.",
}

DIARIZER_HELP = {
    "oss": "pyannote OSS 3.1 — the local default.",
    "api": "pyannote.ai Precision-2 — needs PYANNOTE_AI_KEY; higher accuracy.",
    "vibevoice": "VibeVoice — requires Compute = modal.",
}

# Sensible default event labels per kind. The field is required (pipeline +
# publish), but it's really "a short label shown on the site", so we pre-fill a
# natural default the user can edit. Keys must equal EVENT_KINDS (test-enforced).
MEETING_TYPE_DEFAULTS = {
    "council": "Regular Session",
    "school_board": "Board Meeting",
    "debate": "Debate",
    "forum": "Candidate Forum",
    "community_meeting": "Community Meeting",
    "news_clip": "Interview",
    "press_conference": "Press Conference",
    "other": "",
}
