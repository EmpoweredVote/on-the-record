"""Structure-preserving CREC granule fetch (Slice 1 of the Federal adapter).

Unlike govinfo.fetch_congressional_record_turns (which flattens granules into a
speaker-turn stream and discards granule class/title), this keeps each granule as
a unit — id, class, title, and extracted text — the substrate the Federal
structure branch classifies and mines for votes. Reuses govinfo's URL builders,
pagination, and html_to_text. Pure parsing + injected fetch.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

from . import govinfo


@dataclass
class CrecGranule:
    granule_id: str
    granule_class: str   # "HOUSE" | "SENATE" | "DIGEST" | "EXTENSIONS" | ...
    title: str
    text: str            # extracted from the granule htm via govinfo.html_to_text


def parse_granule_records(json_text: str, chamber: str) -> list[tuple[str, str, str]]:
    """(granule_id, granule_class, title) for chamber granules, in document order.

    Like govinfo.parse_granule_list but KEEPS class + title (the item metadata).
    """
    target = govinfo._CHAMBER_TO_CLASS[chamber.lower()]
    data = json.loads(json_text)
    out: list[tuple[str, str, str]] = []
    for g in data.get("granules", []):
        klass = g.get("granuleClass") or g.get("docClass")
        if klass == target and g.get("granuleId"):
            out.append((g["granuleId"], klass, g.get("title") or ""))
    return out


def fetch_granules(
    date: str,
    chamber: str,
    *,
    fetch: Callable[[str], str] = govinfo._default_fetch,
    api_key: Optional[str] = None,
    max_granules: Optional[int] = None,
) -> Optional[list[CrecGranule]]:
    """Structure-preserving granules for a chamber on a day; None if no Record."""
    key = govinfo._resolve_api_key(api_key)
    pkg = govinfo._package_id(date)

    url = govinfo._granules_url(pkg, "*", key)
    records: list[tuple[str, str, str]] = []
    first = True
    while url:
        try:
            page = fetch(url)
        except Exception:
            if first:
                return None
            print("  crec_structure: granules-list pagination failed mid-way; partial list may be incomplete.")
            break
        first = False
        records.extend(parse_granule_records(page, chamber))
        url = govinfo._with_api_key(govinfo._next_offset_mark(page), key)

    if not records:
        return None
    if max_granules is not None and len(records) > max_granules:
        records = records[:max_granules]

    granules: list[CrecGranule] = []
    for gid, klass, title in records:
        try:
            htm = fetch(govinfo._granule_text_url(pkg, gid, key))
        except Exception:
            print(f"  crec_structure: skipping granule {gid} (text fetch failed).")
            continue
        granules.append(CrecGranule(gid, klass, title, govinfo.html_to_text(htm)))
    return granules or None
