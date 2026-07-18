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
# Handles: "Mr./Mrs./Ms./Miss <Name>[ of <State>]" and "The <ROLE>[ (Mr. <Name>)]".
_DESIGNATION_RE = re.compile(
    r"^(?P<desig>"
    r"(?:Mr|Mrs|Ms|Miss)\.?\s+[A-Z][A-Za-z.'\-]+(?:\s+of\s+[A-Z][A-Za-z ]+?)?"
    r"|The\s+[A-Z][A-Za-z]+(?:\s+[A-Za-z]+)*?(?:\s+\(Mr\.\s+[A-Z][A-Za-z]+\)|\s+pro\s+tempore)?"
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

    Paragraphs that begin with a speaker designation start a new turn; the
    centered title and any non-designation paragraphs are skipped. `start_order`
    offsets `order` so ids stay unique across a day's granules.
    """
    turns: list[CrecTurn] = []
    order = start_order
    for para in _paragraphs(text):
        m = _DESIGNATION_RE.match(para)
        if not m:
            continue
        turns.append(CrecTurn(
            speaker_raw=m.group("desig").strip(),
            text=m.group("rest").strip(),
            granule_id=granule_id,
            order=order,
        ))
        order += 1
    return turns
