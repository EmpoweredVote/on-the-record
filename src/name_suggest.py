"""Name suggestions for one meeting: evidence -> candidates -> lookup -> JSON.

Spec: docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md (slice 2).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .models import Segment
from .name_candidates import Candidate, build_candidates
from .name_evidence import extract_evidence
from .name_lookup import (
    NameDB, RESEARCH_MODEL, Lookup, ResearchCache, ResearchFailed, ResearcherUnavailable, default_fetch, infer_state, norm_name,
    match_local_people, match_politician, match_roster, research, should_research, verify_on_page,
)

OUTPUT_NAME = "name_suggestions.json"

_VTT_TIME = re.compile(r"^\d\d:\d\d:\d\d\.\d+ --> .*$", re.M)
_VTT_TAG = re.compile(r"<[^>]+>")
_VTT_HEADER = re.compile(r"^(WEBVTT.*|NOTE.*|Kind:.*|Language:.*)$", re.M)


def read_captions_text(meeting_dir: Path) -> Optional[str]:
    """Plain caption text: cue timings, inline <..> tags and header lines removed."""
    candidates = [meeting_dir / "source_captions.vtt", meeting_dir / "captions.vtt"]
    candidates += sorted(meeting_dir.glob("captions*.vtt"))
    for p in candidates:
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")
            text = _VTT_TIME.sub("", text)
            text = _VTT_TAG.sub("", text)
            return _VTT_HEADER.sub("", text)
    return None


@dataclass
class Deps:
    db: Optional[NameDB]
    researcher: Callable = research
    fetch: Callable = default_fetch
    cache: Optional[ResearchCache] = None


def _transcript(cand: Candidate, reason: str) -> Lookup:
    return Lookup(name=cand.name, source="transcript", verified=False,
                  affiliation=cand.affiliation, reason=reason)


def suggest_for_candidate(cand: Candidate, *, members: list, state: Optional[str], place: Optional[str],
                          deps: Deps, run: dict) -> Lookup:
    if cand.conflict:
        return _transcript(cand, f"conflict: {cand.conflict}")
    hit = match_roster(cand.name, members) if members else None
    if hit:
        return Lookup(name=hit[0], source="roster", verified=True, politician_id=hit[1])
    if cand.titled:
        pol = match_politician(cand.name, state, deps.db) if deps.db else None
        if pol:
            return Lookup(name=pol["full_name"], source="politician", verified=True,
                          politician_id=pol["politician_id"])
        return _transcript(cand, "titled: no unique politician match" if state else "titled: state unknown")
    if deps.db:
        local = match_local_people(cand.name, deps.db)
        if local:
            return Lookup(name=local["name"], source="local_people", verified=True, local_slug=local["slug"])
    if not should_research(cand.titled, cand.partial, cand.affiliation):
        return _transcript(cand, "not searched: partial name without affiliation")
    if run.get("web_disabled"):
        return _transcript(cand, "web lookup unavailable")

    key = ResearchCache.key(cand.name, cand.affiliation, place)
    cached = deps.cache.get(key) if deps.cache else None
    if cached is not None:
        found = None if cached.get("found") is False else cached
    else:
        try:
            found = deps.researcher(cand.name, None, cand.affiliation, place)
        except ResearcherUnavailable as exc:
            run["web_disabled"] = str(exc)
            return _transcript(cand, "web lookup unavailable")
        except ResearchFailed:
            return _transcript(cand, "web lookup failed \u2014 try again later")
        if deps.cache:
            deps.cache.put(key, found)
    if not found:
        return _transcript(cand, "not found on the web")
    ok, why = verify_on_page(found["name"], found["url"], deps.fetch)
    if not ok:
        return _transcript(cand, why)
    return Lookup(name=found["name"], source="web", verified=True, url=found["url"],
                  affiliation=found.get("affiliation") or cand.affiliation)


def _identity(lk: Lookup) -> Optional[str]:
    if not lk.verified:
        return None
    if lk.politician_id:
        return f"pol:{lk.politician_id}"
    if lk.local_slug:
        return f"local:{lk.local_slug}"
    return f"name:{norm_name(lk.name)}"


def suggest_names(meeting: dict, meeting_dir: Path, *, members: list, deps: Deps,
                  warnings: Optional[list] = None) -> dict:
    segments = [Segment.from_dict(s) for s in meeting.get("segments", [])]
    cands = build_candidates(extract_evidence(segments, read_captions_text(meeting_dir)),
                             meeting.get("event_kind"))
    member_ids = [m.politician_id for m in members if getattr(m, "politician_id", None)]
    state = None
    if deps.db:
        try:
            state = infer_state(member_ids, meeting.get("race_id"), deps.db)
        except Exception:
            state = None
    place = ", ".join(x for x in (meeting.get("city"), state) if x) or None
    run: dict = {"web_disabled": None}
    out = []
    try:
        for label in sorted(cands):
            c = cands[label]
            try:
                lk = suggest_for_candidate(c, members=members, state=state, place=place, deps=deps, run=run)
            except Exception as exc:
                lk = _transcript(c, f"lookup error: {type(exc).__name__}")
            out.append({
                "label": label, "tier": c.tier, "role": c.role, "titled": c.titled, "conflict": c.conflict,
                "spoken_name": c.name,
                "evidence": [{"kind": e.kind, "quote": e.quote} for e in c.evidence],
                "lookup": lk,
            })
    finally:
        if deps.cache:
            deps.cache.save()
    # X4 after lookup: one identity on two labels is a conflict for both.
    groups: dict = {}
    for s in out:
        ident = _identity(s["lookup"])
        if ident:
            groups.setdefault(ident, []).append(s)
    for members_ in groups.values():
        if len(members_) >= 2:
            for s in members_:
                s["conflict"] = "name_on_two_labels"
                s["lookup"] = Lookup(name=s["lookup"].name, source="transcript", verified=False,
                                     affiliation=s["lookup"].affiliation,
                                     reason="conflict: name_on_two_labels")
    for s in out:
        s["lookup"] = s["lookup"].to_dict()
    warns = list(warnings or [])
    if run["web_disabled"]:
        warns.append(run["web_disabled"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": RESEARCH_MODEL, "state": state,
        "warnings": warns,
        "suggestions": out,
    }


def write_suggestions(meeting_dir: Path, result: dict) -> Path:
    path = Path(meeting_dir) / OUTPUT_NAME
    path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return path
