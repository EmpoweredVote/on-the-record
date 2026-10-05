"""Spelling lookup for speaker name suggestions (slice 2).

Spec: docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md
Order: roster -> politicians (titled, by state) -> local_people -> Claude web
researcher -> our own page verification. Network, DB and the claude CLI are
injected so tests never touch them.
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
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


RESEARCH_MODEL = "sonnet"
RESEARCH_TIMEOUT_S = 120
RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "name": {"type": "string"},
        "affiliation": {"type": "string"},
        "url": {"type": "string"},
    },
    "required": ["found"],
}
_UNAVAILABLE_MARKERS = ("authenticate", "oauth", "401", "log in", "login", "usage limit", "rate limit")


class ResearcherUnavailable(RuntimeError):
    """The claude CLI cannot run lookups now (missing, logged out, usage limit)."""


def build_prompt(name: str, title: Optional[str], affiliation: Optional[str], place: Optional[str]) -> str:
    return (
        "Find the exact spelling of the name of a person who spoke at a public meeting.\n"
        f'Spoken name (from an automatic transcript, may be misspelled): "{name}".\n'
        f'Title said: "{title or "none"}".\n'
        f'Stated affiliation: "{affiliation or "none"}".\n'
        f'Meeting place: "{place or "unknown"}".\n'
        "Search the web and open the most authoritative page that names this person "
        "(the organization's own site preferred). Return the exact spelling printed on "
        "that page, the affiliation as printed, and that page's URL. If no page clearly "
        "names this person with this affiliation or place, return found=false. Do not guess."
    )


def research_command(prompt: str, model: str = RESEARCH_MODEL) -> list[str]:
    return [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--json-schema", json.dumps(RESEARCH_SCHEMA),
        "--allowedTools", "WebSearch,WebFetch",
        "--max-turns", "8",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--model", model,
    ]


def run_cli(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def research(name: str, title: Optional[str], affiliation: Optional[str], place: Optional[str], *,
             runner: Callable[[list[str], int], tuple[int, str, str]] = run_cli) -> Optional[dict]:
    """{"name", "affiliation", "url"} for a found person, else None.

    Raises ResearcherUnavailable when the CLI is missing, logged out or out of usage.
    """
    try:
        _rc, out, err = runner(research_command(build_prompt(name, title, affiliation, place)),
                               RESEARCH_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise ResearcherUnavailable("claude CLI not installed") from exc
    except subprocess.TimeoutExpired:
        return None
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if data.get("is_error"):
        msg = f"{data.get('result') or ''} {err or ''}".lower()
        if any(m in msg for m in _UNAVAILABLE_MARKERS):
            raise ResearcherUnavailable(
                "Claude CLI cannot run lookups (not logged in or usage limit) — "
                "run `claude`, then /login, and try again")
        return None
    so = data.get("structured_output")
    if not isinstance(so, dict) or not so.get("found"):
        return None
    found_name, url = (so.get("name") or "").strip(), (so.get("url") or "").strip()
    if not found_name or not url:
        return None
    return {"name": found_name, "affiliation": (so.get("affiliation") or "").strip() or None, "url": url}


def should_research(titled: bool, partial: bool, affiliation: Optional[str]) -> bool:
    """Spec: titled names never go to the web; a partial name needs an affiliation."""
    if titled:
        return False
    return (not partial) or bool(affiliation)


class ResearchCache:
    """JSON cache keyed by (name, affiliation, place). Stores only name/affiliation/url."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    @staticmethod
    def key(name: str, affiliation: Optional[str], place: Optional[str]) -> str:
        return "|".join(norm_name(x or "") for x in (name, affiliation, place))

    def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    def put(self, key: str, value: Optional[dict]) -> None:
        self._data[key] = ({k: value.get(k) for k in ("name", "affiliation", "url")}
                           if value else {"found": False})

    def save(self) -> None:
        from .atomic_io import atomic_write_json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, self._data)
