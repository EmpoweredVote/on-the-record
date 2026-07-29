"""Pure, deterministic mechanical checks. Input = plain dicts. No DB, no I/O."""
import re
from collections import Counter
from typing import Optional
from scripts.models import Finding

# Case-SENSITIVE party labels — capitalized proper nouns, so small-d "democratic"
# (democratic process/allies/governance) and small-r "republican" don't false-match.
_DEM = re.compile(r"\b(Democrat|Democrats|Democratic)\b")
_REP = re.compile(r"\b(Republican|Republicans|GOP)\b")
_PARTISAN = re.compile(r"\b(Democrat|Democrats|Democratic|Republican|Republicans|GOP|MAGA)\b")
_PARTY_PHRASE = re.compile(r"\b(?:my|our) party\b", re.I)
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
CAMPAIGN_SITE = re.compile(r"(for[a-z]+\d{2,4}|20\d\d|campaign)\.(com|org)|(vote|elect)[a-z]+\.(com|org)", re.I)
# Secondary aggregators / encyclopedias — NOT valid sources. They only point to originals,
# so the fix is re-attribution. Deliberately EXCLUDES ballotpedia.org: it reproduces campaign-site
# text verbatim with footnotes to the original, and its Candidate Connection survey answers are
# Ballotpedia-original (see docs/audits/2026-07-25-ballotpedia-triage.md).
AGGREGATOR_SOURCE = re.compile(r"ontheissues\.org|wikipedia\.org", re.I)
# Quiz / questionnaire comparison sites — categorically unquotable, no original to re-attribute to.
# One candidate page mixes canned multiple-choice option text, third-party aggregates
# ("PARTY'S SUPPORT BASE" = surveyed party-affiliated voters) and AI-generated stances (rows
# labelled "CHATGPT"), all under the candidate's name — so no row is quotable regardless of which
# row it came from (see docs/audits/2026-07-25-isidewith-purge.md).
QUIZ_SOURCE = re.compile(r"isidewith\.com", re.I)

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
    # Symmetric framing that names BOTH major parties ("neither Democratic nor Republican",
    # "Democratic, Republican and independent") reveals no side — not a tell.
    if _DEM.search(blind) and _REP.search(blind):
        return None
    m = _PARTISAN.search(blind) or _PARTY_PHRASE.search(blind)
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

def check_invalid_source(r) -> Optional[Finding]:
    url = r.get("source_url") or ""
    if not AGGREGATOR_SOURCE.search(url):
        return None
    return Finding(check_id="invalid-source", level="quote", quote_id=r["id"], topic_key=r["topic_key"],
                   race_id=r["race_id"], candidate=r["candidate"],
                   principle="quotes must cite the ORIGINAL source, not an aggregator",
                   severity="high", fix_class="decision-required",
                   what=f"Source is a secondary aggregator, not an original: {url}",
                   suggested_fix="Follow the aggregator to the candidate's original source (speech/interview/vote/statement), re-source the quote to it; deselect from live until re-sourced.")

def check_unquotable_source(r) -> Optional[Finding]:
    url = r.get("source_url") or ""
    if not QUIZ_SOURCE.search(url):
        return None
    return Finding(check_id="unquotable-source", level="quote", quote_id=r["id"], topic_key=r["topic_key"],
                   race_id=r["race_id"], candidate=r["candidate"],
                   principle="quiz/questionnaire sites publish no quotable rows",
                   severity="high", fix_class="decision-required",
                   what=f"Source is a quiz/questionnaire comparison site: {url}. Such pages mix canned answer-option text, third-party aggregates and AI-generated stances under one candidate's name, so no row is a candidate utterance.",
                   suggested_fix="Delete the row (deselect from live first). There is no original to re-attribute to — if the candidate holds this position, source it from an actual statement instead.")

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
                check_partisan_tell_in_blind, check_source_tier, check_invalid_source,
                check_unquotable_source]
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
