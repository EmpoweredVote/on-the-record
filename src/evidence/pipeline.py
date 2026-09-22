from __future__ import annotations
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from .models import (SourceType, Status, GateResults, EvidenceItem, Lead)
from .triage import classify_domain
from .verify import verbatim_ok
from .extract import extract_quotes
from .crosscheck import crosscheck
from .judge import judge as judge_quote
from .leads import to_lead
from .disposition import decide

_DEFAULT_WORKERS = int(os.environ.get("EVIDENCE_MAX_WORKERS", "6"))
_BARE_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def _concurrent_map(fn, items, max_workers=None) -> list:
    """Map fn over items with a bounded thread pool, returning results in INPUT
    order (ThreadPoolExecutor.map preserves order). Falls back to a sequential
    list comprehension for a single item or workers<=1, so unit tests and the
    common single-quote source stay pool-free and deterministic."""
    items = list(items)
    workers = max_workers or _DEFAULT_WORKERS
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


@dataclass
class Providers:
    extractor: object
    crosschecker: object
    judge: object


def _dropped(pid, url, cited_via, reason) -> EvidenceItem:
    return EvidenceItem(politician_id=pid, issue="", evidence_type="quote",
        verbatim_text="", source_url=url, cited_via=cited_via, context="",
        deep_link=url, source_type=SourceType.SCORECARD_QUIZ.value
        if reason == "scorecard_quiz" else SourceType.DEAD.value,
        gates=GateResults(verbatim=False), status=Status.DROPPED.value,
        status_reasons=[reason], provenance={})


def _evaluate_quote(cand, source_text, *, politician_id, source_url, cited_via,
                    deep_link, source_type, providers, candidate_name, prov,
                    crosscheck_text=None, definitional_primary=False) -> "EvidenceItem":
    if not verbatim_ok(cand.text, source_text):
        return EvidenceItem(politician_id=politician_id, issue=cand.issue,
            evidence_type="quote", verbatim_text=cand.text, source_url=source_url,
            cited_via=cited_via, context=cand.context, deep_link=deep_link,
            source_type=source_type, gates=GateResults(verbatim=False),
            status=Status.DROPPED.value, status_reasons=["verbatim-fail"], provenance=prov)
    cc = crosscheck(cand, crosscheck_text if crosscheck_text is not None else source_text,
                    candidate_name=candidate_name, provider=providers.crosschecker)
    js = judge_quote(cand, provider=providers.judge)
    # For a transcript, own-words and primary are true by construction (the
    # candidate is speaking at their own event) — the trimmed cross-check
    # window can't reliably tell that, so it must not be allowed to veto
    # either gate here. in_context and tag_agree still come from the
    # cross-check, since those aren't definitional.
    own_words = True if definitional_primary else cc.own_words
    primary = True if definitional_primary else cc.primary
    gates = GateResults(verbatim=True, own_words=own_words, in_context=cc.in_context,
        primary=primary, tag_agree=cc.tag_agree, judge_tag_ok=js.tag_ok,
        judge_context_sufficient=js.context_sufficient, judge_dispute_risk=js.dispute_risk,
        judge_mechanism=js.mechanism)
    status, reasons = decide(gates, source_type)
    return EvidenceItem(politician_id=politician_id, issue=cand.issue, evidence_type="quote",
        verbatim_text=cand.text, source_url=source_url, cited_via=cited_via,
        context=cand.context, deep_link=deep_link, source_type=source_type, gates=gates,
        status=status, status_reasons=reasons, provenance=prov)


def run_source(*, politician_id, source_url, cited_via, providers, fetcher,
               candidate_name, batch_id, max_workers=None):
    domain_type = classify_domain(source_url)

    if domain_type in (SourceType.SCORECARD_QUIZ,):
        return [_dropped(politician_id, source_url, cited_via, "scorecard_quiz")], []
    if domain_type is SourceType.VOTE_RECORD:
        return [], []   # out of slice; logged by caller, not emitted
    if domain_type is SourceType.VIDEO_UNFETCHED:
        return [], [Lead(politician_id, "", "", f"video: {source_url}",
                         source_url, source_url)]

    try:
        text = fetcher(source_url) or ""
    except Exception:
        text = ""
    if not text:
        return [_dropped(politician_id, source_url, cited_via, "dead")], []

    prov = {"extractor": getattr(providers.extractor, "model", "extractor"),
            "crosschecker": getattr(providers.crosschecker, "model", "crosschecker"),
            "judge": getattr(providers.judge, "model", "judge"), "batch": batch_id}

    source_type = (SourceType.POINTER.value if domain_type is SourceType.POINTER
                   else SourceType.PRIMARY.value)

    cands = list(extract_quotes(text, candidate_name=candidate_name,
                                provider=providers.extractor))
    def _is_lead(c): return (not c.is_primary_venue) or c.reported_event
    leads = [to_lead(c, politician_id=politician_id, secondary_url=source_url)
             for c in cands if _is_lead(c)]
    to_eval = [c for c in cands if not _is_lead(c)]
    items = _concurrent_map(
        lambda c: _evaluate_quote(c, text, politician_id=politician_id, source_url=source_url,
            cited_via=cited_via, deep_link=source_url, source_type=source_type,
            providers=providers, candidate_name=candidate_name, prov=prov),
        to_eval, max_workers=max_workers)
    return items, leads


def _deep_link(source, quote_text: str) -> str:
    """Point at the transcript segment the quote starts in.
    A bare YouTube video id (how meetings.meetings.video_url stores YouTube
    playback — see publish.resolve_playback) is first expanded to a watch URL.
    YouTube URLs get a `t=<seconds>s` query param (using `&` when the base
    already has a `?`, else `?`); other URLs get a `#t=<seconds>` fragment.
    Falls back to the video/source URL with no timestamp when no segment matches."""
    base = source.video_url or source.source_url or ""
    if _BARE_YOUTUBE_ID.fullmatch(base):
        base = f"https://www.youtube.com/watch?v={base}"
    head = (quote_text or "").strip()[:40]
    for start, text in source.segments:
        if head and head in text:
            if "youtube.com" in base or "youtu.be" in base:
                sep = "&" if "?" in base else "?"
                return f"{base}{sep}t={int(start)}s"
            return f"{base}#t={int(start)}"
    return base


def _local_window(full_text: str, quote_text: str, radius: int = 800) -> str:
    """A small slice of full_text around the quote, for the independent
    cross-check — avoids re-sending the whole (often 60K+ char) transcript
    per quote. Falls back to the first 2*radius chars if the quote's head
    can't be located verbatim (e.g. the extractor lightly reworded it)."""
    head = (quote_text or "").strip()[:40]
    i = full_text.find(head) if head else -1
    if i < 0:
        return full_text[:2 * radius]
    return full_text[max(0, i - radius): i + len(quote_text) + radius]


def run_transcript_source(source, *, politician_id, providers, candidate_name, batch_id,
                          max_workers=None):
    prov = {"extractor": getattr(providers.extractor, "model", "extractor"),
            "crosschecker": getattr(providers.crosschecker, "model", "crosschecker"),
            "judge": getattr(providers.judge, "model", "judge"), "batch": batch_id}
    cands = list(extract_quotes(source.full_text, candidate_name=candidate_name,
                                provider=providers.extractor))
    items = _concurrent_map(
        lambda c: _evaluate_quote(c, source.full_text, politician_id=politician_id,
            source_url=source.source_url, cited_via=source.meeting_id,
            deep_link=_deep_link(source, c.text), source_type=SourceType.PRIMARY.value,
            providers=providers, candidate_name=candidate_name, prov=prov,
            crosscheck_text=_local_window(source.full_text, c.text), definitional_primary=True),
        cands, max_workers=max_workers)
    return items, []


def run_candidate(*, politician_id, candidate_name, sources, providers, fetcher,
                  batch_id, transcript_sources=None, max_workers=None):
    all_items, all_leads = [], []
    for source_url, cited_via in sources:
        items, leads = run_source(politician_id=politician_id, source_url=source_url,
            cited_via=cited_via, providers=providers, fetcher=fetcher,
            candidate_name=candidate_name, batch_id=batch_id, max_workers=max_workers)
        all_items += items
        all_leads += leads
    for ts in (transcript_sources or []):
        it, ld = run_transcript_source(ts, politician_id=politician_id,
            providers=providers, candidate_name=candidate_name, batch_id=batch_id,
            max_workers=max_workers)
        all_items += it
        all_leads += ld
    return all_items, all_leads
