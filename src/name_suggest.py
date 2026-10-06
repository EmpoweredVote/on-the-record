"""Name suggestions for one meeting: evidence -> candidates -> lookup -> JSON.

Spec: docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md (slice 2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .atomic_io import atomic_write_json
from .models import Segment
from .name_candidates import Candidate, build_candidates
from .name_evidence import extract_evidence
from .name_lookup import (
    PARTIAL_EXPANDED, NameDB, RESEARCH_MODEL, Lookup, ResearchCache, ResearchFailed, ResearcherUnavailable, default_fetch, infer_state,
    match_local_people, match_politician, match_roster, norm_name, research, should_research, verify_web_result,
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


def meeting_context(meeting: dict) -> Optional[str]:
    """"Title (kind; org, org)" from the meeting's own fields; None when there is nothing."""
    head = (meeting.get("title") or meeting.get("meeting_type") or "").strip()
    orgs = []
    for o in meeting.get("event_orgs") or []:
        n = (o.get("name") if isinstance(o, dict) else o) or ""
        if str(n).strip():
            orgs.append(str(n).strip())
    parts = [p for p in ((meeting.get("event_kind") or "").strip(), ", ".join(orgs)) if p]
    if head and parts:
        return f"{head} ({'; '.join(parts)})"
    return head or "; ".join(parts) or None


def _intro(cand: Candidate) -> Optional[str]:
    return next((e.quote for e in cand.evidence if e.kind == "E1" and e.quote), None)


def _transcript(cand: Candidate, reason: str) -> Lookup:
    return Lookup(name=cand.name, source="transcript", verified=False,
                  affiliation=cand.affiliation, reason=reason)


def suggest_for_candidate(cand: Candidate, *, members: list, state: Optional[str], place: Optional[str],
                          deps: Deps, run: dict, intro: Optional[str] = None,
                          context: Optional[str] = None) -> Lookup:
    if cand.conflict:
        return _transcript(cand, f"conflict: {cand.conflict}")
    hit = match_roster(cand.name, members) if members else None
    if hit:
        if cand.partial and not cand.titled:
            # A bare surname/first name with no title is too weak to link to a member.
            return Lookup(name=hit[0], source="roster", verified=False, reason="possible roster match")
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

    key = ResearchCache.key(cand.name, cand.affiliation, place, intro=intro, context=context)
    cached = deps.cache.get(key) if deps.cache else None
    if cached is not None:
        found = None if cached.get("found") is False else cached
    else:
        try:
            found = deps.researcher(cand.name, None, cand.affiliation, place,
                                     intro=intro, context=context)
        except ResearcherUnavailable as exc:
            run["web_disabled"] = str(exc)
            return _transcript(cand, "web lookup unavailable")
        except ResearchFailed:
            return _transcript(cand, "web lookup failed \u2014 try again later")
        if deps.cache:
            deps.cache.put(key, found)
    if not found:
        return _transcript(cand, "not found on the web")
    ok, why, aff_ok = verify_web_result(cand.name, cand.partial, cand.affiliation, found, deps.fetch)
    affiliation = (found.get("affiliation") or cand.affiliation) if aff_ok else cand.affiliation
    if not ok:
        if why == PARTIAL_EXPANDED:
            return Lookup(name=found["name"], source="web", verified=False, url=found["url"],
                          affiliation=affiliation, reason=why)
        return _transcript(cand, why)
    return Lookup(name=found["name"], source="web", verified=True, url=found["url"], affiliation=affiliation)


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
    context = meeting_context(meeting)
    run: dict = {"web_disabled": None}
    out = []
    try:
        for label in sorted(cands):
            c = cands[label]
            try:
                lk = suggest_for_candidate(c, members=members, state=state, place=place, deps=deps, run=run,
                                         intro=_intro(c), context=context)
            except Exception as exc:
                lk = _transcript(c, f"lookup error: {type(exc).__name__}")
            out.append({
                "label": label, "tier": c.tier, "role": c.role, "titled": c.titled, "partial": c.partial,
                "conflict": c.conflict,
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
        lk = s["lookup"]
        s["prefill_name"] = lk.name if (lk.verified and s["conflict"] is None) else None
        s["lookup"] = lk.to_dict()
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
    atomic_write_json(path, result, indent=1)
    return path
