import re

EVENT_KINDS = (
    "council",
    "school_board",
    "debate",
    "forum",
    "community_meeting",
    "news_clip",
    "press_conference",
    "podcast",
    "other",
)


def validate_event_kind(value: str) -> str:
    normalized = value.strip()
    if normalized not in EVENT_KINDS:
        allowed = ", ".join(EVENT_KINDS)
        raise ValueError(
            f"Unknown event kind {value!r}; allowed values: {allowed}"
        )
    return normalized


# --- Local (non-roster) person role vocabularies --------------------------
# Roles offered when creating a local person during review. The FIRST entry is
# the default chosen on empty input. Civic meetings surface staff/public-comment/
# official/presenter; campaign-style events keep candidate/moderator/panelist.
_CIVIC_ROLES = ("public_comment", "staff", "official", "presenter")
_CAMPAIGN_ROLES = ("candidate", "moderator", "panelist")

LOCAL_ROLE_SETS = {
    "council": _CIVIC_ROLES,
    "school_board": _CIVIC_ROLES,
    "community_meeting": _CIVIC_ROLES,
    "press_conference": ("official", "staff", "presenter", "public_comment"),
    "podcast": _CAMPAIGN_ROLES,
    "forum": _CAMPAIGN_ROLES,
    "debate": _CAMPAIGN_ROLES,
}

# Fallback for unknown/None kinds: every role, so the prompt never blocks a
# valid choice or silently coerces it to the wrong default.
DEFAULT_LOCAL_ROLES = (
    "candidate", "moderator", "panelist",
    "public_comment", "staff", "official", "presenter",
)


def local_roles_for(event_kind):
    """Ordered role options for a local person at the given event kind.

    The first element is the empty-input default.
    """
    return LOCAL_ROLE_SETS.get(event_kind or "", DEFAULT_LOCAL_ROLES)


def resolve_local_role(raw, event_kind):
    """Map a prompt response to a role string for the given event kind.

    - empty -> the first (default) role for the kind
    - a 1-based number in range -> that listed option
    - an out-of-range number -> the default (not a stray numeric role)
    - anything else -> a normalized free-text custom role (lowercased,
      runs of non-alphanumerics collapsed to single underscores)
    """
    roles = local_roles_for(event_kind)
    raw = (raw or "").strip()
    if not raw:
        return roles[0]
    if raw.isdigit():
        n = int(raw)
        return roles[n - 1] if 1 <= n <= len(roles) else roles[0]
    norm = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return norm or roles[0]
