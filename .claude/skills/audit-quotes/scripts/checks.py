"""Pure, deterministic mechanical checks. Input = plain dicts. No DB, no I/O."""
import re
from collections import Counter
from typing import Optional
from scripts.models import Finding

_PARTISAN = re.compile(r"\b(Democrat|Democratic|Republican|GOP|MAGA|my party)\b", re.I)
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
CAMPAIGN_SITE = re.compile(r"(for[a-z]+\d{2,4}|20\d\d|campaign)\.(com|org)|(vote|elect)[a-z]+\.(com|org)", re.I)

def check_note_quality(r) -> Optional[Finding]:
    note = (r.get("editor_note") or "").strip()
    base = dict(level="quote", quote_id=r["id"], topic_key=r["topic_key"],
                race_id=r["race_id"], candidate=r["candidate"])
    if not note:
        return Finding(check_id="note-missing", principle="editor_note required",
                       severity="high", fix_class="guided",
                       what="editor_note is empty.",
                       suggested_fix="Write a 1-2 sentence note: why this quote + Compass-stance alignment + any edits.",
                       **base)
    if "§" in note or re.search(r"\btier-?\d\b", note, re.I):
        return Finding(check_id="note-section-ref", principle="notes are self-contained",
                       severity="medium", fix_class="guided",
                       what="editor_note cites internal section numbers / jargon.",
                       suggested_fix="Rewrite without §-refs or 'tier-N'; keep it human-readable.", **base)
    if len(_SENTENCE_END.findall(note)) > 2:
        return Finding(check_id="note-too-long", principle="editor_note <= 2 sentences",
                       severity="low", fix_class="guided",
                       what="editor_note is longer than 2 sentences.",
                       suggested_fix="Tighten to <=2 sentences unless heavy editing must be explained.", **base)
    return None

def check_deid_present(r) -> Optional[Finding]:
    if (r.get("deidentified_text") or "").strip():
        return None
    return Finding(check_id="deid-missing", level="quote", quote_id=r["id"], topic_key=r["topic_key"],
                   race_id=r["race_id"], candidate=r["candidate"], principle="blind text required",
                   severity="high", fix_class="guided",
                   what="deidentified_text is null; row is not admin-selectable and has no blind card.",
                   suggested_fix="Draft the blind version (canonical + extra de-id; verbatim copy only if nothing identifying), confirm, then apply.")

def check_trailing_ellipsis(r) -> Optional[Finding]:
    txt = (r.get("quote_text") or "").rstrip()
    if txt.endswith("…") or txt.endswith("..."):
        return Finding(check_id="trailing-ellipsis", level="quote", quote_id=r["id"], topic_key=r["topic_key"],
                       race_id=r["race_id"], candidate=r["candidate"], principle="no trailing ellipsis",
                       severity="low", fix_class="mechanical",
                       what="Quote ends with a trailing ellipsis.",
                       suggested_fix="Remove the trailing ellipsis.",
                       fix_op={"kind": "regex_sub", "field": "quote_text", "pattern": r"\s*(…|\.\.\.)\s*$", "repl": ""})
    return None

def check_partisan_tell_in_blind(r) -> Optional[Finding]:
    blind = r.get("deidentified_text") or ""
    m = _PARTISAN.search(blind)
    if not m:
        return None
    return Finding(check_id="partisan-tell", level="quote", quote_id=r["id"], topic_key=r["topic_key"],
                   race_id=r["race_id"], candidate=r["candidate"], principle="no partisan tell on blind card",
                   severity="high", fix_class="guided",
                   what=f"Blind text contains a partisan/side tell: '{m.group(0)}'.",
                   suggested_fix="Drop the partisan word on the blind card (or neutralize to '[the current administration]'); draft, confirm, then apply.")

def check_source_tier(r) -> Optional[Finding]:
    url = r.get("source_url") or ""
    if "youtube.com" in url or "youtu.be" in url:
        return None
    if CAMPAIGN_SITE.search(url):
        return Finding(check_id="source-tier-4", level="quote", quote_id=r["id"], topic_key=r["topic_key"],
                       race_id=r["race_id"], candidate=r["candidate"], principle="prefer tier 1-2 spoken sources",
                       severity="medium", fix_class="decision-required",
                       what=f"Source looks like a campaign/written page (tier 4): {url}",
                       suggested_fix="Confirm it's a verbatim first-person sentence (not a summary); prefer a tier-1 spoken quote if available.")
    return None

def topic_live_count(group) -> Optional[Finding]:
    counts = Counter(q["candidate"] for q in group["quotes"] if q.get("readrank_selected"))
    dupes = {c: n for c, n in counts.items() if n > 1}
    if not dupes:
        return None
    return Finding(check_id="multiple-live", level="topic", topic_key=group["topic_key"], race_id=group["race_id"],
                   principle="one live quote per candidate per topic", severity="high", fix_class="decision-required",
                   what=f"Candidate(s) with more than one live quote in this topic: {dict(dupes)}",
                   suggested_fix="Demote all but one live quote per candidate to draft.")

def topic_min_candidates(group) -> Optional[Finding]:
    cands = {q["candidate"] for q in group["quotes"] if q.get("readrank_selected")}
    if len(cands) >= 2:
        return None
    return Finding(check_id="not-rankable", level="topic", topic_key=group["topic_key"], race_id=group["race_id"],
                   principle=">=2 candidates to be rankable", severity="medium", fix_class="decision-required",
                   what=f"Only {len(cands)} candidate(s) live on this topic; not a valid head-to-head.",
                   suggested_fix="Source a second candidate's on-question quote, or drop the topic from the race.")

QUOTE_CHECKS = [check_note_quality, check_deid_present, check_trailing_ellipsis,
                check_partisan_tell_in_blind, check_source_tier]
TOPIC_CHECKS = [topic_live_count, topic_min_candidates]

def run_mechanical(rows) -> list:
    findings = []
    for r in rows:
        for chk in QUOTE_CHECKS:
            f = chk(r)
            if f: findings.append(f)
    groups = {}
    for r in rows:
        groups.setdefault((r["race_id"], r["topic_key"]), {"race_id": r["race_id"], "topic_key": r["topic_key"], "quotes": []})["quotes"].append(r)
    for g in groups.values():
        for chk in TOPIC_CHECKS:
            f = chk(g)
            if f: findings.append(f)
    return findings
