"""Per-race scoped-search lane: comparable-source hubs -> RawItems (Slice 2B).

Turns the hubs `hubs_for_race` resolved for a race into `RawItem`s by running
one scoped Tavily search per hub (domain-scoped `site:` search, or a filled
`query_template`). Pointer-only hubs (VOTE411/LWV — ToS bars scraping/copy
without written permission) are never searched. No LLM/verification here;
that happens downstream in classify (current-cycle etc).
"""
from __future__ import annotations

from src.discovery.models import RawItem
from src.discovery.web_search import tavily_search

MAX_ITEMS_PER_HUB = 2


def _is_pointer_only(hub) -> bool:
    return hub.tos_bucket == "vote411-lwv" or "pointer-only" in (hub.notes or "").lower()


def _query_for_hub(hub, *, candidates, locality, year) -> "str | None":
    if hub.query_template:
        return hub.query_template.replace("<locality>", locality or "").replace("<year>", year or "")
    if hub.domain:
        query = f"site:{hub.domain} " + " ".join(candidates or [locality or ""])
        if year:
            query += f" {year}"
        return query.strip()
    return None


def raw_items_for_race(hubs_for_this_race, *, candidates, locality, year,
                        budget: int = 6) -> "list[RawItem]":
    items: list[RawItem] = []
    searches_done = 0

    for hub in hubs_for_this_race:
        if _is_pointer_only(hub):
            continue
        if searches_done >= budget:
            break

        query = _query_for_hub(hub, candidates=candidates, locality=locality, year=year)
        if query is None:
            continue

        results = tavily_search(query)
        searches_done += 1

        if hub.domain:
            results = [r for r in results if hub.domain in (r.get("url") or "")]

        for r in results[:MAX_ITEMS_PER_HUB]:
            items.append(RawItem(
                url=r["url"],
                title=r.get("title"),
                description=r.get("content"),
                via="hub",
            ))

    return items
