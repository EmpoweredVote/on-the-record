"""Spelling lookup for speaker name suggestions (slice 2).

Spec: docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md
Order: roster -> politicians (titled, by state) -> local_people -> Claude web
researcher -> our own page verification. Network, DB and the claude CLI are
injected so tests never touch them.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Callable, Optional, Union
from urllib.parse import urlparse

MAX_PAGE_BYTES = 2_000_000


@dataclass
class Lookup:
    name: str
    source: str                       # roster | politician | local_people | web | transcript
    verified: bool
    politician_id: Optional[str] = None
    local_slug: Optional[str] = None
    url: Optional[str] = None
    affiliation: Optional[str] = None
    reason: Optional[str] = None      # why not verified / not searched

    def to_dict(self) -> dict:
        d = asdict(self)
        if not self.verified:
            d["url"] = None  # never show an unverified source
        return d


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\xa0", " ").lower()
    s = re.sub(r"[^\w' ]+", " ", s)
    return " ".join(s.split())


def _strip_possessive(normed: str) -> str:
    out = []
    for tok in normed.split():
        tok = tok.strip("'")
        if tok.endswith("'s"):
            tok = tok[:-2]
        if tok:
            out.append(tok)
    return " ".join(out)


def page_text(html: Union[str, bytes]) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return soup.get_text(" ")


def name_on_page(name: str, text: str) -> bool:
    n = norm_name(name)
    if not n:
        return False
    t = norm_name(text)
    return f" {n} " in f" {t} " or f" {n} " in f" {_strip_possessive(t)} "


_OK_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


def default_fetch(url: str) -> bytes:
    import requests

    from .download import BROWSER_USER_AGENT

    with requests.get(url, timeout=(10, 20), headers={"User-Agent": BROWSER_USER_AGENT}, stream=True) as resp:
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype and ctype not in _OK_TYPES:
            raise ValueError(f"non-html content: {ctype}")
        return resp.raw.read(MAX_PAGE_BYTES, decode_content=True)


def verify_on_page(
    name: str, url: str, fetch: Callable[[str], Union[str, bytes]] = default_fetch
) -> tuple[bool, Optional[str]]:
    try:
        if urlparse(url or "").scheme not in ("http", "https"):
            return False, "bad url"
    except Exception:  # noqa: BLE001 - malformed url
        return False, "bad url"
    try:
        html = fetch(url)
    except Exception as exc:  # noqa: BLE001 - any fetch failure just means "not verified"
        return False, f"fetch failed: {exc}"[:200]
    try:
        return (True, None) if name_on_page(name, page_text(html)) else (False, "name not on page")
    except Exception as exc:  # noqa: BLE001 - never raise from verification
        return False, f"verify failed: {exc}"[:200]
