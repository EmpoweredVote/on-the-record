"""Stage-2 LLM verdict for discovered items.

The verdict ranks items for the human skim; it never claims speaker
identity — that stays in the real pipeline post-approval. Structured
output = regex-extract-then-json.loads (house pattern, agenda_interpret.py).
"""
from __future__ import annotations

import json
import re

from src import config
from src.discovery.models import RawItem, Verdict

_SYSTEM = (
    "You screen newly discovered political media for an ingestion pipeline. "
    "You judge from metadata and (sometimes) unlabeled captions. "
    "Respond ONLY with a single JSON object."
)

ALLOWED_KINDS = {"debate", "forum", "news_clip", "press_conference",
                 "podcast", "community_meeting", "other"}
ALLOWED_ROUTES = {"ingest", "quote_source"}

_PROMPT_TEMPLATE = """A tracked election race and a newly found video/audio item are below.
Decide whether this item is an ORIGINAL source of the candidates' own spoken words
(a debate, forum, town hall, long-form interview, press conference, or podcast
appearance) — as opposed to a news package ABOUT them, an ad, or a clip compilation.

Race: {race_label}
Tracked candidates:
{roster}

Item metadata:
- title: {title}
- channel: {channel}
- duration_seconds: {duration}
- published: {published}
- description (truncated): {description}
{captions_block}
Source tiers: 1 = debate/candidate forum; 2 = news interview; 3 = prepared public
remarks (stump speech, town hall, testimony); 4 = candidate-bylined written.
"original_vs_clip": "original" = the full event / substantial segment where the
candidate speaks at length; "clip" = a short excerpt or a package about them.
Set "relevant" to true ONLY for original sources of the candidates' own words —
i.e. when original_vs_clip is "original". News packages ABOUT candidates, campaign
ads, and highlight/clip compilations are relevant=false even when the candidate
appears or is quoted in them.
If captions are provided, judge DISCOURSE SHAPE: sustained first-person policy
speech and moderator/Q&A signatures suggest an original event; third-person
anchor narration with soundbites suggests a news package. Do not guess who is
speaking — only whether candidate speech is present at length.

Respond with JSON only:
{{"relevant": true/false, "confidence": 0.0-1.0,
  "candidates_present": ["names from the tracked list that appear"],
  "event_kind": "debate|forum|news_clip|press_conference|podcast|community_meeting|other",
  "source_tier": 1-4, "original_vs_clip": "original|clip",
  "route": "ingest|quote_source",
  "why": "one sentence citing your strongest evidence"}}"""


def build_prompt(item: RawItem, *, race_label: str, roster_names: list,
                 captions_excerpt: "str | None" = None) -> str:
    roster = "\n".join(f"- {n}" for n in roster_names) or "- (none)"
    captions_block = ""
    if captions_excerpt:
        captions_block = f"\nUnlabeled auto-captions excerpt:\n\"\"\"\n{captions_excerpt}\n\"\"\"\n"
    desc = (item.description or "")[:1500]
    return _PROMPT_TEMPLATE.format(
        race_label=race_label, roster=roster, title=item.title or "(none)",
        channel=item.channel_name or "(unknown)",
        duration=item.duration_seconds if item.duration_seconds is not None else "(unknown)",
        published=item.published_at or "(unknown)", description=desc or "(none)",
        captions_block=captions_block,
    )


def parse_verdict(text: str) -> Verdict:
    match = re.search(r"\{[\s\S]*\}", text or "")
    if not match:
        return Verdict(False, 0.0, rejected_reason="no JSON in reply")
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return Verdict(False, 0.0, rejected_reason="malformed JSON")
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    kind = data.get("event_kind")
    tier = data.get("source_tier")
    try:
        tier = int(tier) if tier is not None else None
    except (TypeError, ValueError):
        tier = None
    ovc = data.get("original_vs_clip")
    route = data.get("route")
    return Verdict(
        relevant=bool(data.get("relevant")),
        confidence=confidence,
        candidates_present=[str(n) for n in data.get("candidates_present") or []],
        event_kind_guess=kind if kind in ALLOWED_KINDS else None,
        source_tier_guess=tier if tier in (1, 2, 3, 4) else None,
        original_vs_clip=ovc if ovc in ("original", "clip") else None,
        route=route if route in ALLOWED_ROUTES else "ingest",
        why=str(data.get("why") or ""),
    )


def vtt_to_text(vtt: str, max_chars: int = 6000) -> str:
    lines = []
    for line in (vtt or "").splitlines():
        line = line.strip()
        if (not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE"))
                or "-->" in line or line.isdigit()):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        while lines and lines[-1] in line:
            lines.pop()          # incoming settled line supersedes prior fragments
        if lines and line in lines[-1]:
            continue             # fragment already inside the last settled line
        lines.append(line)
    return " ".join(lines)[:max_chars]


def _filter_candidates(verdict: Verdict, roster_names: list) -> Verdict:
    allowed = {n.lower() for n in roster_names}
    verdict.candidates_present = [n for n in verdict.candidates_present
                                  if n.lower() in allowed]
    return verdict


def classify_item(provider, item: RawItem, *, race_label: str, roster_names: list,
                  captions_fetcher=None) -> Verdict:
    """One LLM pass; a second pass with captions when confidence lands in the
    mid band and a captions_fetcher is supplied. captions_fetcher(url) returns
    raw VTT text or None."""
    text = provider.complete(
        build_prompt(item, race_label=race_label, roster_names=roster_names),
        max_tokens=config.DISCOVERY_CLASSIFY_MAX_TOKENS, temperature=0.0, system=_SYSTEM)
    verdict = parse_verdict(text)
    low, high = config.DISCOVERY_CAPTIONS_BAND
    if (captions_fetcher is not None and verdict.rejected_reason is None
            and low <= verdict.confidence < high):
        vtt = captions_fetcher(item.url)
        if vtt:
            text2 = provider.complete(
                build_prompt(item, race_label=race_label, roster_names=roster_names,
                             captions_excerpt=vtt_to_text(vtt)),
                max_tokens=config.DISCOVERY_CLASSIFY_MAX_TOKENS, temperature=0.0,
                system=_SYSTEM)
            second = parse_verdict(text2)
            if second.rejected_reason is None:
                return _filter_candidates(second, roster_names)
    return _filter_candidates(verdict, roster_names)
