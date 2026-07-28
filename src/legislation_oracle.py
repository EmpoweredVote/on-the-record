"""Final-action oracle backed by Bloomington's council legislation pages.

Page pattern (captured 2026-07-28, tests/fixtures/legislation/):
  https://bloomington.in.gov/council/legislation/{Type}/{year}/{year}-{NN}
  where {Type} is one of the landing page's type paths (Ordinance,
  Resolution, Appropriation%20Ordinance) and the number is zero-padded to
  two digits (2026-01 is 200; 2026-1 is 404).

A published page carries a single final-action table row:

    <tr><th>Final</th>
        <td>2026-06-10</td>
        <td>pass</td>
        <td>7-2 (Asare, Rosenbarger)</td>
    </tr>

Legislation without a final disposition has NO page yet — the URL 404s —
so "pending" shows up as a fetch error or a body with no Final row; both
return None (abstain, never guess).

House style: pure parsing + injected fetch (see src/onboard.py).
"""
from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from bs4 import BeautifulSoup

BASE_URL = "https://bloomington.in.gov/council/legislation"

# Landing-page type list -> URL path segment (already percent-encoded).
_TYPE_PATHS = {
    "ordinance": "Ordinance",
    "resolution": "Resolution",
    "appropriation ordinance": "Appropriation%20Ordinance",
}

_REF_RE = re.compile(r"^\s*(.+?)\s+(\d{4})-(\d+)\s*$")

# Site outcome word -> our outcome vocabulary.
_OUTCOME_MAP = {
    "pass": "passed",
    "passed": "passed",
    "adopt": "passed",
    "adopted": "passed",
    "fail": "failed",
    "failed": "failed",
    "reject": "failed",
    "rejected": "failed",
    "defeat": "failed",
    "defeated": "failed",
    "postpone": "continued",
    "postponed": "continued",
    "continue": "continued",
    "continued": "continued",
    "withdraw": "pulled",
    "withdrawn": "pulled",
}


def _default_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed gov host)
        return resp.read().decode("utf-8")


@dataclass(frozen=True)
class FinalAction:
    action_date: str  # verbatim, ISO on the real pages ("2026-06-10")
    outcome: str  # our vocabulary: passed | failed | continued | pulled
    tally: Optional[str] = None  # verbatim, e.g. "7-2 (Asare, Rosenbarger)"


def build_legislation_url(legislation_ref: str) -> Optional[str]:
    """"Ordinance 2026-14" -> the item's legislation-page URL, or None for
    a ref whose type isn't in the site's type list (or a malformed ref)."""
    m = _REF_RE.match(legislation_ref or "")
    if not m:
        return None
    kind = re.sub(r"\s+", " ", m.group(1)).strip().lower()
    path = _TYPE_PATHS.get(kind)
    if path is None:
        return None
    year, number = m.group(2), int(m.group(3))
    return f"{BASE_URL}/{path}/{year}/{year}-{number:02d}"


def _parse_final_action(html: str) -> Optional[FinalAction]:
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None
    for th in soup.find_all("th"):
        if th.get_text(strip=True) != "Final":
            continue
        row = th.find_parent("tr")
        if row is None:
            continue
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) < 2 or not cells[0]:
            return None
        outcome = _OUTCOME_MAP.get(cells[1].strip().lower())
        if outcome is None:
            return None
        tally = cells[2] if len(cells) > 2 and cells[2] else None
        return FinalAction(action_date=cells[0], outcome=outcome, tally=tally)
    return None


def fetch_final_action(
    legislation_ref: str,
    *,
    fetch: Callable[[str], str] = _default_fetch,
) -> Optional[FinalAction]:
    """The final action recorded on the ref's legislation page, or None when
    the page doesn't exist yet (pending), the fetch fails, or the page has
    no recognizable Final row."""
    url = build_legislation_url(legislation_ref)
    if url is None:
        return None
    try:
        html = fetch(url)
    except Exception:
        return None
    return _parse_final_action(html)
