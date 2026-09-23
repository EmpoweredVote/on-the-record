from __future__ import annotations
import re
import unicodedata

_WS = re.compile(r"\s+")
_TRANS = {0x2019: "'", 0x2018: "'", 0x201c: '"', 0x201d: '"',
          0x2013: "-", 0x2014: "-"}


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").translate(_TRANS)
    s = s.replace("…", "...")
    return _WS.sub(" ", s).strip().lower()


def quote_runs(quote: str) -> list:
    """The normalized, ellipsis-separated runs a quote must match, in order."""
    return [p for p in (seg.strip() for seg in normalize(quote).split("...")) if p]


def find_raw(hay: str, run: str) -> int:
    """Index in the RAW hay where a normalized run (e.g. a quote_runs item)
    first matches normalize(hay), or -1. normalize changes lengths (NFKC,
    whitespace collapse, "…"→"..."), so the normalized hit is mapped back by
    bisecting on raw prefixes: len(normalize(hay[:k])) never shrinks as k
    grows, and the first raw char whose prefix covers the hit is its start.
    Using normalize itself keeps this in step with verbatim_ok's rules."""
    j = normalize(hay).find(run) if run else -1
    if j < 0:
        return -1
    lo, hi = 0, len(hay)
    while lo < hi:
        mid = (lo + hi) // 2
        if len(normalize(hay[:mid + 1])) > j:
            hi = mid
        else:
            lo = mid + 1
    return lo


def verbatim_ok(quote: str, source_text: str) -> bool:
    parts = quote_runs(quote)
    if not parts:
        return False
    hay = normalize(source_text)
    pos = 0
    for p in parts:
        i = hay.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True
