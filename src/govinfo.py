# src/govinfo.py
"""GovInfo Congressional Record (CREC) fetch.

Turns a (date, chamber) into the official Congressional Record's ordered speaker
turns for that chamber's floor proceedings. This is a *speaker-identity* source,
not a transcript substitute: CREC is non-verbatim and has no timestamps, but it
records exactly who spoke, in what order.

Parsing is pure and network-free; the only network primitive is an injected
`fetch`. Mirrors src/podcast.py and src/brightspot.py.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

_API_ROOT = "https://api.govinfo.gov"
_CHAMBER_TO_CLASS = {"house": "HOUSE", "senate": "SENATE"}


@dataclass
class CrecTurn:
    speaker_raw: str      # "Mr. Cotton", "The PRESIDING OFFICER (Mr. Cotton)"
    text: str             # remarks attributed to that speaker
    granule_id: str       # source granule (provenance)
    order: int            # 0-based position across the day's matched granules


def _package_id(date: str) -> str:
    """'YYYY-MM-DD' -> 'CREC-YYYY-MM-DD'."""
    return f"CREC-{date}"


def _resolve_api_key(api_key: Optional[str]) -> str:
    """arg -> GOVINFO_API_KEY env -> 'DEMO_KEY'."""
    return api_key or os.environ.get("GOVINFO_API_KEY") or "DEMO_KEY"


def parse_granule_list(json_text: str, chamber: str) -> list[str]:
    """Granule ids whose granuleClass matches the chamber, in document order.

    Reads `granuleClass`, falling back to `docClass` for older payloads.
    DIGEST / EXTENSIONS are excluded by only matching the chamber's class.
    """
    target = _CHAMBER_TO_CLASS[chamber.lower()]
    data = json.loads(json_text)
    ids: list[str] = []
    for g in data.get("granules", []):
        klass = g.get("granuleClass") or g.get("docClass")
        if klass == target and g.get("granuleId"):
            ids.append(g["granuleId"])
    return ids


def _next_offset_mark(json_text: str) -> Optional[str]:
    """Absolute URL of the next granules page, or None when exhausted."""
    return json.loads(json_text).get("nextPage") or None


def html_to_text(htm: str) -> str:
    """Readable text from a CREC granule htm.

    The content is a single <pre> block; BeautifulSoup keeps its text (dropping
    the inline <a> tag) and unescapes entities. Falls back to whole-document text
    if no <pre> is present.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(htm, "html.parser")
    pre = soup.find("pre")
    return (pre.get_text() if pre else soup.get_text())


# A paragraph-initial speaker designation, up to the period that ends it.
# Handles: "Mr./Mrs./Ms./Miss <Name>[ of <State>]" and "The <PRESIDING ROLE>
# [ pro tempore][ (Mr. <Name>)]". The role is a CLOSED whitelist of real presiding
# roles — an earlier "The <any capitalized words>" form falsely matched prose
# paragraphs beginning "The" (e.g. "The Trump administration has expanded...").
_DESIGNATION_RE = re.compile(
    r"^(?P<desig>"
    r"(?:Mr|Mrs|Ms|Miss)\.?\s+[A-Z][A-Za-z.'\-]+(?:\s+of\s+[A-Z][A-Za-z ]+?)?"
    r"|The\s+(?:(?i:acting)\s+)?"
    r"(?i:presiding\s+officer|speaker|vice\s+president|president|chief\s+justice|chair|clerk)"
    r"(?:\s+pro\s+tempore)?"
    r"(?:\s+\((?:Mr|Mrs|Ms|Miss)\.\s+[A-Z][A-Za-z]+\))?"
    r")\.\s+(?P<rest>.+)$",
    re.S,
)

# Lines that are pure page/header furniture, dropped before paragraph assembly.
_FURNITURE_RE = re.compile(
    r"^\s*(\[.*\]|From the Congressional Record Online.*|_{5,})\s*$"
)


def _paragraphs(text: str) -> list[str]:
    """Reflow CREC body text into paragraphs.

    A new paragraph starts on a line indented >=2 spaces; flush-left lines are
    wrapped continuations and join onto the current paragraph with a single
    space. Furniture lines and blank lines flush the current paragraph.
    """
    paras: list[str] = []
    cur: list[str] = []

    def flush():
        if cur:
            paras.append(" ".join(w.strip() for w in cur).strip())
            cur.clear()

    for line in text.splitlines():
        if not line.strip() or _FURNITURE_RE.match(line):
            flush()
            continue
        if re.match(r"^ {2,}\S", line):   # indented -> new paragraph
            flush()
            cur.append(line)
        else:                              # flush-left -> continuation
            cur.append(line)
    flush()
    return paras


def parse_granule_turns(text: str, granule_id: str, start_order: int) -> list[CrecTurn]:
    """Ordered speaker turns in one granule's text.

    A paragraph beginning with a speaker designation starts a new turn. A
    subsequent non-designation paragraph is a continuation of the current
    speaker's speech (CREC tags only the first paragraph) and is appended — but
    only when it contains lowercase prose, so an ALL-CAPS section heading (or a
    title before the first speaker) never pollutes a turn. `start_order` offsets
    `order` so ids stay unique across a day's granules.
    """
    turns: list[CrecTurn] = []
    order = start_order
    for para in _paragraphs(text):
        m = _DESIGNATION_RE.match(para)
        if m:
            turns.append(CrecTurn(
                speaker_raw=m.group("desig").strip(),
                text=m.group("rest").strip(),
                granule_id=granule_id,
                order=order,
            ))
            order += 1
        elif turns and any(c.islower() for c in para):
            # continuation paragraph of the current speech (headings are ALL-CAPS)
            turns[-1].text = f"{turns[-1].text} {para}".strip()
    return turns


def _granules_url(package_id: str, offset_mark: str, api_key: str, page_size: int = 100) -> str:
    return (f"{_API_ROOT}/packages/{package_id}/granules"
            f"?offsetMark={offset_mark}&pageSize={page_size}&api_key={api_key}")


def _granule_text_url(package_id: str, granule_id: str, api_key: str) -> str:
    return f"{_API_ROOT}/packages/{package_id}/granules/{granule_id}/htm?api_key={api_key}"


def _default_fetch(url: str) -> str:
    import requests
    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def _with_api_key(url: Optional[str], api_key: str) -> Optional[str]:
    """Ensure `url` carries the api_key. GovInfo's `nextPage` value omits it, so
    following it verbatim 401s and truncates every day to its first page."""
    if not url or "api_key=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}api_key={api_key}"


def _list_matching_granule_ids(package_id, chamber, api_key, fetch) -> Optional[list[str]]:
    """All chamber granule ids across every page, or None if the first page fails
    (a recess day / missing package is normal, not an error)."""
    url = _granules_url(package_id, "*", api_key)
    ids: list[str] = []
    first = True
    while url:
        try:
            page = fetch(url)
        except Exception:
            if first:
                return None      # no Record for this day
            print("  govinfo: granules-list pagination failed mid-way; "
                  "returning a partial granule list (results may be incomplete).")
            break                # partial pagination: stop, keep what we have
        first = False
        ids.extend(parse_granule_list(page, chamber))
        url = _with_api_key(_next_offset_mark(page), api_key)
    return ids


def fetch_congressional_record_turns(
    date: str,
    chamber: str,
    *,
    fetch: Callable[[str], str] = _default_fetch,
    api_key: Optional[str] = None,
    max_granules: Optional[int] = None,
) -> Optional[list[CrecTurn]]:
    """Ordered speaker turns of the Congressional Record for a chamber on a day.

    Returns None when there is no Record (recess), no matching granules, or every
    granule text fetch fails. Never returns a silently-partial Record as if
    complete. `max_granules` caps requests (rate-limit / demo guard) and logs
    when it truncates.
    """
    key = _resolve_api_key(api_key)
    package_id = _package_id(date)

    ids = _list_matching_granule_ids(package_id, chamber, key, fetch)
    if not ids:
        return None

    if max_granules is not None and len(ids) > max_granules:
        print(f"  govinfo: truncating {len(ids)} granules to first {max_granules} "
              f"(max_granules guard).")
        ids = ids[:max_granules]

    turns: list[CrecTurn] = []
    any_ok = False
    for gid in ids:
        try:
            htm = fetch(_granule_text_url(package_id, gid, key))
        except Exception:
            print(f"  govinfo: skipping granule {gid} (text fetch failed).")
            continue
        any_ok = True
        turns.extend(parse_granule_turns(html_to_text(htm), gid, start_order=len(turns)))

    if not any_ok:
        return None
    return turns


def format_turns_text(turns: list[CrecTurn]) -> str:
    """Render turns as 'Speaker: text' blocks, blank-line separated."""
    return "\n\n".join(f"{t.speaker_raw}: {t.text}" for t in turns)


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch the Congressional Record's speaker turns for a "
                    "chamber on a date (needs GOVINFO_API_KEY for real use).")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("chamber", choices=["house", "senate"])
    parser.add_argument("--max-granules", type=int, default=None)
    parser.add_argument("--out", default=None,
                        help="write transcript text to this path instead of stdout")
    args = parser.parse_args(argv)

    turns = fetch_congressional_record_turns(
        args.date, args.chamber, max_granules=args.max_granules)
    if turns is None:
        print("No Congressional Record found for that date/chamber "
              "(recess day, not yet published, or rate-limited).")
        return 1

    text = format_turns_text(turns)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {len(turns)} turns to {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
