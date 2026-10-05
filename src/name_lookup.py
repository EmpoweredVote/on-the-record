"""Spelling lookup for speaker name suggestions (slice 2).

Spec: docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md
Order: roster -> politicians (titled, by state) -> local_people -> Claude web
researcher -> our own page verification. Network, DB and the claude CLI are
injected so tests never touch them.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Callable, Optional
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
    s = s.replace("’", "'").replace("\xa0", " ").lower()
    s = re.sub(r"[^\w' ]+", " ", s)
    return " ".join(s.split())


def page_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return soup.get_text(" ")


def name_on_page(name: str, text: str) -> bool:
    n = norm_name(name)
    return bool(n) and f" {n} " in f" {norm_name(text)} "


def default_fetch(url: str) -> str:
    import requests

    from .download import BROWSER_USER_AGENT

    resp = requests.get(url, timeout=(10, 20), headers={"User-Agent": BROWSER_USER_AGENT}, stream=True)
    resp.raise_for_status()
    body = resp.raw.read(MAX_PAGE_BYTES, decode_content=True)
    return body.decode(resp.encoding or "utf-8", errors="replace")


def verify_on_page(name: str, url: str, fetch: Callable[[str], str] = default_fetch) -> tuple[bool, Optional[str]]:
    if urlparse(url or "").scheme not in ("http", "https"):
        return False, "bad url"
    try:
        html = fetch(url)
    except Exception as exc:  # noqa: BLE001 - any fetch failure just means "not verified"
        return False, f"fetch failed: {exc}"[:200]
    return (True, None) if name_on_page(name, page_text(html)) else (False, "name not on page")
