"""Auto-approve trusted outlets' news-clip rows as quote sources.

Safety invariant: this only ever sets status='approved', route='quote_source'.
It never ingests. Lane parity with src.discovery.lanes.content_lane: a row is
eligible iff its outlet is trusted, it is pending, it has a race, it is a clip,
and its event kind is NOT a formal-event kind (so it is the 'news_clip' lane).
Barred outlets are still eligible — the bar is on ingest, not on quoting.
"""
from __future__ import annotations

from src.discovery.lanes import FORMAL_EVENT_KINDS

# Reusable WHERE fragment (no leading "where") — shared by the sweep below and
# asserted on directly in tests, so the eligibility rule has one home. Deliberately
# does NOT reference o.ingest_barred: the ingest bar is enforced elsewhere (the
# approve->ingest gate), never here.
ELIGIBLE_LANE_SQL = (
    "o.trusted and d.status = 'pending' and d.race_id is not null "
    "and d.original_vs_clip = 'clip' "
    "and coalesce(d.event_kind_guess, '') not in %s"
)


def auto_approve_pending(cur, outlet_id: "str | None" = None) -> int:
    """Flip every eligible pending row (see ELIGIBLE_LANE_SQL) to approved /
    quote_source. Takes an open cursor; the caller commits — this lets both
    poll_discovery (whole-table sweep) and the GUI's trust action (one outlet,
    via outlet_id) share the same transaction as their other writes. Returns
    the number of rows changed."""
    params = [tuple(FORMAL_EVENT_KINDS)]
    outlet_filter = ""
    if outlet_id is not None:
        outlet_filter = " and o.id = %s::uuid"
        params.append(outlet_id)
    cur.execute(f"""
        update essentials.discovered_sources d
        set status = 'approved', route = 'quote_source',
            status_reason = 'auto: trusted outlet', reviewed_at = now()
        from essentials.source_outlets o
        where d.outlet_id = o.id and {ELIGIBLE_LANE_SQL}{outlet_filter}
    """, tuple(params))
    return cur.rowcount
