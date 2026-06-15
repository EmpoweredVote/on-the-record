EVENT_KINDS = (
    "council",
    "school_board",
    "debate",
    "forum",
    "community_meeting",
    "news_clip",
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
