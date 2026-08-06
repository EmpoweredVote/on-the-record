"""One-shot re-classify of pending discovered_sources rows after a tier
prompt change (spec 2026-08-05-source-tier-recalibration). Cursor- and
provider-injected so it composes in one transaction (house pattern, db.py).

Updates ONLY the classifier-guess fields (source_tier_guess,
event_kind_guess, confidence, why) — never status, route, or provenance.
Metadata-only: the fetch-time captions/page peek is not re-run."""
from __future__ import annotations

from src.discovery.classify import classify_item
from src.discovery.models import RawItem


def fetch_pending(cur) -> list:
    cur.execute("""
        select d.id::text, d.url, d.title, d.description_snippet,
               d.channel_name, d.duration_seconds, d.published_at::text,
               d.race_id::text, p.race_label, d.source_tier_guess
        from essentials.discovered_sources d
        left join essentials.readrank_race_pipeline p on p.race_id = d.race_id
        where d.status = 'pending'
        order by d.created_at
    """)
    return cur.fetchall()


def roster_for_race(cur, race_id: str) -> list:
    cur.execute("""
        select rc.full_name from essentials.race_candidates rc
        where rc.race_id = %s::uuid and rc.full_name is not null
          and coalesce(rc.candidate_status, 'active')
              not in ('withdrawn', 'removed')
        order by rc.full_name
    """, (race_id,))
    return [r[0] for r in cur.fetchall()]


def reclassify_row(cur, provider, row) -> tuple:
    """Returns (old_tier, new_tier); new_tier is None when the verdict
    failed to parse or carried no usable tier (row left untouched)."""
    (row_id, url, title, desc, channel, duration, published,
     race_id, race_label, old_tier) = row
    roster = roster_for_race(cur, race_id) if race_id else []
    item = RawItem(url=url, title=title, description=desc,
                   channel_name=channel, duration_seconds=duration,
                   published_at=published, via="search")
    verdict = classify_item(provider, item,
                            race_label=race_label or "(unknown race)",
                            roster_names=roster, peek_fetcher=None)
    if verdict.rejected_reason is not None or verdict.source_tier_guess is None:
        return old_tier, None
    cur.execute("""
        update essentials.discovered_sources
        set source_tier_guess = %s, event_kind_guess = %s,
            confidence = %s, why = %s
        where id = %s::uuid
    """, (verdict.source_tier_guess, verdict.event_kind_guess,
          verdict.confidence, verdict.why, row_id))
    return old_tier, verdict.source_tier_guess
