"""Name suggestions as review views and accept actions (slice 3). Pure.

Spec: docs/superpowers/specs/2026-10-06-speaker-name-suggestions-review-design.md
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

SUGGESTIONS_NAME = "name_suggestions.json"
DEFAULT_ROLE = "public_comment"


@dataclass
class SuggestionView:
    label: str
    name: str
    state: str                     # acceptable | unverified | info
    source: str
    url: Optional[str]
    domain: Optional[str]
    quotes: list[str] = field(default_factory=list)
    role: Optional[str] = None
    reason: Optional[str] = None
    politician_id: Optional[str] = None
    local_slug: Optional[str] = None
    tier: Optional[str] = None
    verified: bool = False


@dataclass
class AcceptAction:
    kind: str                      # link | local
    name: str
    politician_id: Optional[str] = None
    slug: Optional[str] = None
    role: Optional[str] = None


def load_suggestions(meeting_dir: Path) -> tuple[list[dict], list[str]]:
    try:
        data = json.loads((Path(meeting_dir) / SUGGESTIONS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    recs = [r for r in data.get("suggestions") or [] if isinstance(r, dict) and r.get("label")]
    warns = [str(w) for w in data.get("warnings") or []]
    return recs, warns


def _safe_url(url) -> Optional[str]:
    """The url only if it is http(s). It comes from web/LLM output and becomes a
    clickable link in the review GUI, so javascript:, data: and the like are dropped."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        scheme = urlparse(url.strip()).scheme.lower()
    except ValueError:
        return None
    return url.strip() if scheme in ("http", "https") else None


def to_view(rec: dict) -> Optional[SuggestionView]:
    lk = rec.get("lookup") or {}
    name = (lk.get("name") or rec.get("spoken_name") or "").strip()
    if not name:
        return None
    if rec.get("prefill_name"):
        state, reason = "acceptable", None
    elif rec.get("conflict"):
        state = "info"
        reason = ("also suggested for another speaker" if rec["conflict"] == "name_on_two_labels"
                  else str(rec["conflict"]).replace("_", " "))
    elif rec.get("partial"):
        state, reason = "info", "first name only"
    else:
        state, reason = "unverified", lk.get("reason")
    url = _safe_url(lk.get("url"))
    host = (urlparse(url).hostname or "") if url else ""
    return SuggestionView(
        label=rec["label"], name=rec.get("prefill_name") or name, state=state, source=lk.get("source") or "transcript",
        url=url, domain=(host[4:] if host.startswith("www.") else host) or None,
        quotes=[e.get("quote", "") for e in rec.get("evidence") or [] if e.get("kind") != "E4"][:2],
        role=rec.get("role"), reason=reason, politician_id=lk.get("politician_id"),
        local_slug=lk.get("local_slug"), tier=rec.get("tier"), verified=bool(lk.get("verified")),
    )


def _get(m, key):
    return m.get(key) if isinstance(m, dict) else getattr(m, key, None)


def is_unnamed_mapping(m) -> bool:
    if m is None:
        return True
    if _get(m, "speaker_status") == "non_speaker":
        return False
    if _get(m, "politician_id") or _get(m, "politician_slug") or _get(m, "local_slug"):
        return False
    name = (_get(m, "speaker_name") or "").strip()
    return not name or _get(m, "speaker_status") == "unidentified" or name.lower().startswith("unidentified")


def views_for_unnamed(meeting_dir: Path, mappings: dict, labels: Iterable[str]
                      ) -> tuple[dict[str, SuggestionView], list[str]]:
    recs, warns = load_suggestions(meeting_dir)
    live = set(labels)
    out: dict[str, SuggestionView] = {}
    for r in recs:
        lab = r["label"]
        if lab in live and is_unnamed_mapping(mappings.get(lab)):
            v = to_view(r)
            if v:
                out[lab] = v
    return out, warns


def accept_action(view: SuggestionView, default_slug: str) -> Optional[AcceptAction]:
    if view.state != "acceptable":
        return None
    if view.politician_id:
        return AcceptAction("link", view.name, politician_id=view.politician_id)
    slug = view.local_slug or default_slug
    return AcceptAction("local", view.name, slug=slug, role=view.role or DEFAULT_ROLE)


def apply_action_to_mappings(mappings, segments, label: str, action: AcceptAction,
                             event_kind: Optional[str]) -> None:
    """In-memory equivalent of the GUI's apply_link / apply_make_local_person
    (clear status -> rename -> assign), for the terminal review."""
    from . import review
    from .event_kinds import resolve_local_role

    review.clear_speaker_status(mappings, segments, label)
    review.rename_speaker(mappings, segments, label, action.name, roster=None)
    if action.kind == "link":
        review.link_speaker(mappings, label, None, action.politician_id)
    else:
        review.assign_local_person(mappings, label, action.slug, resolve_local_role(action.role, event_kind))


def terminal_suggestion_line(view: SuggestionView) -> str:
    src = f"{view.source} · {view.domain}" if view.domain else view.source
    if view.state == "acceptable":
        return f"Suggested: {view.name} (verified, {src}) — [Y] to accept"
    if view.state == "unverified":
        return f"Suggested (not verified): {view.name} — {view.reason or 'not verified'}"
    return f"Possible: {view.name} — {view.reason or ''}".rstrip(" —")
