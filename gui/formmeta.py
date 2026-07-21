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
    "floor": "A US House/Senate floor proceeding — legislative session with roll-call votes.",
    "news_clip": "A journalist interviewing a subject.",
    "press_conference": "A subject making a statement and taking questions.",
    "podcast": "A podcast or radio interview episode (audio-only).",
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

# Sensible default event labels per kind. The field lives under "Advanced" and
# feeds the URL slug + the compact site label; it auto-fills from the kind so the
# operator rarely touches it. Every value must be NON-EMPTY (a blank label under a
# collapsed section would be an invisible, un-fixable required-field trap). Keys
# must equal EVENT_KINDS (test-enforced).
MEETING_TYPE_DEFAULTS = {
    "council": "Regular Session",
    "school_board": "Board Meeting",
    "debate": "Debate",
    "forum": "Candidate Forum",
    "community_meeting": "Community Meeting",
    "floor": "House Floor",
    "news_clip": "Interview",
    "press_conference": "Press Conference",
    "podcast": "Podcast",
    "other": "Recording",
}

# Which optional fields the new-meeting form shows for each event kind. The
# always-shown fields (source, date, event_kind, title, event_orgs) are NOT
# listed here. Keys must equal EVENT_KINDS (test-enforced). Consumed by the
# template + new_meeting.js to hide inapplicable inputs.
FIELDS_BY_KIND = {
    "council":           ("city", "body"),
    "school_board":      ("city", "body"),
    "debate":            ("race",),
    "forum":             ("race",),
    "community_meeting": ("city",),
    "floor":             ("crec_chamber",),
    "news_clip":         ("guest", "race"),
    "press_conference":  ("guest", "race"),
    "podcast":           ("guest", "race"),
    "other":             ("city",),
}

# GUI form defaults (the CLI keeps its own defaults). Modal is the compute the
# operator reaches for most; oss is the local-quality-default diarizer.
DEFAULT_COMPUTE = "modal"
DEFAULT_DIARIZER = "oss"


def humanize_kind(kind: str) -> str:
    """Display label for an event kind: 'news_clip' -> 'News Clip'. Display only;
    the raw snake_case value stays authoritative for form values / filtering.
    Empty/None -> '' (callers keep their own '—' fallback)."""
    return (kind or "").replace("_", " ").title()
