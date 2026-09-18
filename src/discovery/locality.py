"""Derive a per-race locality string for local-type hub searches.

Pure + DB-free. The governments.name value is populated for federal/statewide
races too (e.g. 'United States Federal Government', 'State of Arizona') and can
be NULL for some statewide offices, so a real locality needs BOTH a local race
level AND a government name that is an actual place.
"""
from __future__ import annotations

import re

from src.race_level import race_level

_LOCAL_LEVELS = {"county", "local", "school"}
_FED_SENTINEL = "united states federal government"
_STATE_PREFIX = "state of "
# A trailing ", <region>, US"/"USA" geography tail (e.g. ", California, US").
_GEO_TAIL = re.compile(r",\s*[^,]+,\s*(?:us|usa)\s*$", re.I)


def local_query_locality(position_name: "str | None",
                         government_name: "str | None") -> "str | None":
    if race_level(position_name) not in _LOCAL_LEVELS:
        return None
    name = (government_name or "").strip()
    if not name:
        return None
    low = name.lower()
    if low == _FED_SENTINEL or low.startswith(_STATE_PREFIX):
        return None
    cleaned = _GEO_TAIL.sub("", name).strip()
    return cleaned or None
