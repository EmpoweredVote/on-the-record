from __future__ import annotations
from .models import Lead, QuoteCandidate


def to_lead(cand: QuoteCandidate, *, politician_id: str, secondary_url: str) -> Lead:
    return Lead(
        politician_id=politician_id,
        reported_text=cand.text,
        issue=cand.issue,
        event=cand.reported_event or (cand.setting or "unspecified event"),
        secondary_url=secondary_url,
        primary_handle=cand.primary_handle)
