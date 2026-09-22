import json
import threading
import time
from src.evidence.pipeline import Providers, run_source, run_candidate, run_transcript_source, _concurrent_map
from src.evidence.data import TranscriptSource
from src.evidence.models import Status, SourceType

class FP:
    def __init__(self, r): self._r=list(r); self.prompts=[]; self._lock=threading.Lock()
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        with self._lock:
            self.prompts.append(prompt)
            return self._r.pop(0)

class RoleFP:
    """Returns a fixed reply regardless of call order — keyed by nothing, safe under threads."""
    def __init__(self, reply): self._reply=reply; self.prompts=[]; self._lock=threading.Lock()
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        with self._lock: self.prompts.append(prompt)
        return self._reply

SRC = "On her site Bass wrote: We will build 30,000 units of housing this term."

def _providers(extract, cross, judge):
    return Providers(extractor=FP([extract]), crosschecker=FP([cross]), judge=FP([judge]))

def test_primary_quote_that_passes_is_green():
    extract = json.dumps({"quotes": [{"text":"We will build 30,000 units of housing",
        "context": SRC, "issue":"housing","date":"2026","setting":"campaign site",
        "is_own_words":True,"is_primary_venue":True,"reported_event":None,
        "primary_handle":None}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,
                        "tag_ok":True,"issue":"housing","notes":""})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9,"forward_looking":0.9})
    items, leads = run_source(politician_id="p1",
        source_url="https://karenbass.com/housing", cited_via=None,
        providers=_providers(extract, cross, jud), fetcher=lambda u: SRC,
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.GREEN.value
    assert items[0].source_type == SourceType.PRIMARY.value

def test_primary_quote_with_only_a_goal_is_flagged_no_mechanism():
    # High tag/context, low dispute risk — but the judge scores mechanism low
    # (e.g. the quote names only a GOAL, not a concrete policy lever), so the
    # item must be FLAGGED, not GREEN. This is the whole point of the gate.
    extract = json.dumps({"quotes": [{"text":"We will build 30,000 units of housing",
        "context": SRC, "issue":"housing","date":"2026","setting":"campaign site",
        "is_own_words":True,"is_primary_venue":True,"reported_event":None,
        "primary_handle":None}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,
                        "tag_ok":True,"issue":"housing","notes":""})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.1})
    items, leads = run_source(politician_id="p1",
        source_url="https://karenbass.com/housing", cited_via=None,
        providers=_providers(extract, cross, jud), fetcher=lambda u: SRC,
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.FLAGGED.value
    assert "judge:no-mechanism" in items[0].status_reasons

def test_quote_with_indefensible_tag_is_flagged_tag_agree():
    # The cross-checker judges the extractor's proposed tag indefensible
    # (tag_ok: false) — a genuine substance mismatch, not a wording quibble —
    # so the item must be FLAGGED on crosscheck:tag_agree.
    extract = json.dumps({"quotes": [{"text":"We will build 30,000 units of housing",
        "context": SRC, "issue":"foreign policy","date":"2026","setting":"campaign site",
        "is_own_words":True,"is_primary_venue":True,"reported_event":None,
        "primary_handle":None}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,
                        "tag_ok":False,"issue":"housing","notes":"off-topic tag"})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9,"forward_looking":0.9})
    items, leads = run_source(politician_id="p1",
        source_url="https://karenbass.com/housing", cited_via=None,
        providers=_providers(extract, cross, jud), fetcher=lambda u: SRC,
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.FLAGGED.value
    assert "crosscheck:tag_agree" in items[0].status_reasons

def test_reported_secondary_becomes_lead_not_item():
    extract = json.dumps({"quotes": [{"text":"I will end street homelessness",
        "context":"At the debate she said…","issue":"homelessness","date":"2026",
        "setting":"debate","is_own_words":True,"is_primary_venue":False,
        "reported_event":"LAist debate, 2026-05-01","primary_handle":None}]})
    items, leads = run_source(politician_id="p1",
        source_url="https://laist.com/story", cited_via=None,
        providers=_providers(extract, "{}", "{}"), fetcher=lambda u: "…",
        candidate_name="Karen Bass", batch_id="b1")
    assert items == [] and len(leads) == 1
    assert "NOT verified against a primary" in leads[0].note

def test_hallucinated_quote_is_dropped():
    extract = json.dumps({"quotes": [{"text":"I will privatize every park",
        "context":"…","issue":"parks","date":"2026","setting":"site",
        "is_own_words":True,"is_primary_venue":True,"reported_event":None,
        "primary_handle":None}]})
    items, leads = run_source(politician_id="p1",
        source_url="https://karenbass.com/x", cited_via=None,
        providers=_providers(extract, "{}", "{}"), fetcher=lambda u: SRC,
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.DROPPED.value
    assert "verbatim-fail" in items[0].status_reasons

def test_scorecard_domain_dropped_without_llm():
    items, leads = run_source(politician_id="p1",
        source_url="https://lcv.org/scorecard", cited_via=None,
        providers=_providers("{}", "{}", "{}"), fetcher=lambda u: "should not fetch",
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.DROPPED.value

def test_video_domain_becomes_lead_without_fetch_or_llm():
    called = {"fetched": False}
    def fetcher(u):
        called["fetched"] = True
        return "should not fetch"
    items, leads = run_source(politician_id="p1",
        source_url="https://youtu.be/abc", cited_via=None,
        providers=_providers("{}", "{}", "{}"), fetcher=fetcher,
        candidate_name="Karen Bass", batch_id="b1")
    assert items == [] and len(leads) == 1
    assert called["fetched"] is False
    assert "youtu.be/abc" in (leads[0].event or "") or "youtu.be/abc" in (leads[0].primary_handle or "")

def test_pointer_source_quote_is_flagged_never_green():
    extract = json.dumps({"quotes": [{"text": "We will build 30,000 units of housing",
        "context": SRC, "issue": "housing", "date": "2026", "setting": "wiki",
        "is_own_words": True, "is_primary_venue": True, "reported_event": None,
        "primary_handle": None}]})
    cross = json.dumps({"own_words": True, "in_context": True, "primary": True,
                        "tag_ok": True, "issue": "housing", "notes": ""})
    jud = json.dumps({"tag_ok": 0.9, "context_sufficient": 0.9, "dispute_risk": 0.1})
    items, leads = run_source(politician_id="p1",
        source_url="https://en.wikipedia.org/wiki/Karen_Bass", cited_via=None,
        providers=_providers(extract, cross, jud), fetcher=lambda u: SRC,
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1
    assert items[0].source_type == SourceType.POINTER.value
    assert items[0].status == Status.FLAGGED.value
    assert "not-primary" in items[0].status_reasons

def test_run_candidate_aggregates_across_sources():
    extract = json.dumps({"quotes": [{"text": "We will build 30,000 units of housing",
        "context": SRC, "issue": "housing", "date": "2026", "setting": "site",
        "is_own_words": True, "is_primary_venue": True, "reported_event": None,
        "primary_handle": None}]})
    cross = json.dumps({"own_words": True, "in_context": True, "primary": True,
                        "tag_ok": True, "issue": "housing", "notes": ""})
    jud = json.dumps({"tag_ok": 0.9, "context_sufficient": 0.9, "dispute_risk": 0.1, "mechanism": 0.9, "forward_looking": 0.9})
    providers = Providers(extractor=FP([extract]), crosschecker=FP([cross]), judge=FP([jud]))
    items, leads = run_candidate(politician_id="p1", candidate_name="Karen Bass",
        sources=[("https://karenbass.com/housing", None), ("https://lcv.org/x", None)],
        providers=providers, fetcher=lambda u: SRC, batch_id="b1")
    assert len(items) == 2
    statuses = {it.status for it in items}
    assert Status.GREEN.value in statuses and Status.DROPPED.value in statuses

def test_fetcher_that_raises_is_treated_as_dead_not_crash():
    def boom(url):
        raise RuntimeError("403 Forbidden")
    items, leads = run_source(politician_id="p1",
        source_url="https://www.ontheissues.org/x", cited_via=None,
        providers=_providers("{}", "{}", "{}"), fetcher=boom,
        candidate_name="Karen Bass", batch_id="b1")
    assert leads == []
    assert len(items) == 1 and items[0].status == Status.DROPPED.value
    assert "dead" in items[0].status_reasons


def _tsrc(video_url="https://youtu.be/x"):
    turn = "We will build 40,000 units by cutting permit timelines."
    full = f"Moderator: What will you do on housing?\nKaren Bass: {turn}"
    return TranscriptSource(meeting_id="m1", source_url="https://site/m1",
        video_url=video_url, title="Debate", event_kind="debate",
        full_text=full, segments=[(20.0, turn)])

def test_transcript_quote_is_green_primary_with_timestamp_deeplink():
    # Bare youtu.be URL has no existing query string, so the deep link must
    # start the query with "?", not blindly append "&t=..." (which would
    # produce an invalid, non-seeking URL like "youtu.be/x&t=20s").
    turn = "We will build 40,000 units by cutting permit timelines."
    extract = json.dumps({"quotes": [{"text": turn, "context": "housing question",
        "issue":"housing","is_own_words":True,"is_primary_venue":True}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,"tag_ok":True})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9,"forward_looking":0.9})
    items, leads = run_transcript_source(_tsrc(), politician_id="p1",
        providers=_providers(extract, cross, jud), candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.GREEN.value
    assert items[0].source_type == SourceType.PRIMARY.value
    assert items[0].deep_link == "https://youtu.be/x?t=20s"

def test_transcript_deeplink_uses_ampersand_when_youtube_url_already_has_query():
    # youtube.com/watch?v=... already has a "?", so the timestamp param must
    # be joined with "&", not a second "?".
    turn = "We will build 40,000 units by cutting permit timelines."
    extract = json.dumps({"quotes": [{"text": turn, "context": "housing question",
        "issue":"housing","is_own_words":True,"is_primary_venue":True}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,"tag_ok":True})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9,"forward_looking":0.9})
    src = _tsrc(video_url="https://www.youtube.com/watch?v=abc")
    items, leads = run_transcript_source(src, politician_id="p1",
        providers=_providers(extract, cross, jud), candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.GREEN.value
    assert items[0].deep_link == "https://www.youtube.com/watch?v=abc&t=20s"

def test_transcript_deeplink_uses_fragment_for_non_youtube_base():
    # A non-YouTube base (e.g. a meeting page) has no seek query param, so
    # the deep link must use a "#t=" fragment instead.
    turn = "We will build 40,000 units by cutting permit timelines."
    extract = json.dumps({"quotes": [{"text": turn, "context": "housing question",
        "issue":"housing","is_own_words":True,"is_primary_venue":True}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,"tag_ok":True})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9,"forward_looking":0.9})
    src = _tsrc(video_url="https://site/m1/watch")
    items, leads = run_transcript_source(src, politician_id="p1",
        providers=_providers(extract, cross, jud), candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.GREEN.value
    assert items[0].deep_link == "https://site/m1/watch#t=20"

def test_transcript_reworded_quote_drops_verbatim_fail():
    extract = json.dumps({"quotes": [{"text":"As mayor she plans to construct homes.",
        "context":"x","issue":"housing","is_own_words":True,"is_primary_venue":True}]})
    items, leads = run_transcript_source(_tsrc(), politician_id="p1",
        providers=_providers(extract, "{}", "{}"), candidate_name="Karen Bass", batch_id="b1")
    assert items[0].status == Status.DROPPED.value and "verbatim-fail" in items[0].status_reasons

def test_transcript_crosscheck_sees_trimmed_window_not_full_text():
    # full_text is deliberately > extract_quotes' 12000-char chunk_size, so
    # extraction runs over 2 windows (this repo now extracts per-window —
    # see chunk_text in src/evidence/extract.py); the quote falls only in
    # the second window (verified empirically), so the FP extractor is
    # queued with an empty-quotes reply for the first window and the real
    # one for the second. That's orthogonal to what this test targets: the
    # crosschecker must see only a small local window, not the full text.
    turn = "We will build 40,000 units by cutting permit timelines."
    big = ("UNRELATED FILLER. " * 900) + f"Karen Bass: {turn} " + ("MORE FILLER. " * 150)
    src = TranscriptSource(meeting_id="m1", source_url="https://site/m1",
        video_url="https://youtu.be/x", title="Debate", event_kind="debate",
        full_text=big, segments=[(20.0, turn)])
    extract_empty = json.dumps({"quotes": []})
    extract_real = json.dumps({"quotes":[{"text":turn,"context":"housing","issue":"housing",
        "is_own_words":True,"is_primary_venue":True}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,"tag_ok":True})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9,"forward_looking":0.9})
    prov = Providers(extractor=FP([extract_empty, extract_real]),
                     crosschecker=FP([cross]), judge=FP([jud]))
    items, _ = run_transcript_source(src, politician_id="p1", providers=prov,
                                     candidate_name="Karen Bass", batch_id="b1")
    # the crosschecker prompt it saw must contain the quote but be far smaller than full_text
    seen = prov.crosschecker.prompts[0]
    assert turn in seen and len(seen) < 4000 and len(seen) < len(big) // 5
    assert items[0].status == Status.GREEN.value


def test_transcript_definitional_primary_greens_despite_crosscheck_primary_false():
    turn = "We will build 40,000 units by cutting permit timelines."
    src = TranscriptSource(meeting_id="m1", source_url="https://site/m1",
        video_url="https://youtu.be/x", title="Debate", event_kind="debate",
        full_text=f"Karen Bass: {turn}", segments=[(20.0, turn)])
    extract = json.dumps({"quotes":[{"text":turn,"context":"housing","issue":"housing",
        "is_own_words":True,"is_primary_venue":True}]})
    # cross-checker says NOT primary and NOT own_words (the window-starved failure), but in_context/tag ok
    cross = json.dumps({"own_words":False,"in_context":True,"primary":False,"tag_ok":True})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9,"forward_looking":0.9})
    items,_ = run_transcript_source(src, politician_id="p1",
        providers=_providers(extract, cross, jud), candidate_name="Karen Bass", batch_id="b1")
    assert items[0].status == Status.GREEN.value          # definitional primary/own_words override
    assert items[0].gates.primary is True and items[0].gates.own_words is True
    assert items[0].gates.in_context is True              # crosscheck's in_context still used


def test_concurrent_map_preserves_input_order():
    # later items finish sooner; ex.map must still return in input order
    def fn(n): time.sleep((5 - n) * 0.02); return n
    assert _concurrent_map(fn, [0,1,2,3,4], max_workers=4) == [0,1,2,3,4]

def test_concurrent_map_runs_every_item():
    seen = []
    import threading; lock = threading.Lock()
    def fn(x):
        with lock: seen.append(x)
        return x * 2
    out = _concurrent_map(fn, [1,2,3], max_workers=3)
    assert out == [2,4,6] and sorted(seen) == [1,2,3]

def test_concurrent_map_sequential_fastpath():
    calls = []
    def fn(x): calls.append(x); return x
    assert _concurrent_map(fn, [7], max_workers=8) == [7]      # single item → no pool
    assert _concurrent_map(fn, [1,2], max_workers=1) == [1,2]  # workers<=1 → sequential


def _role_providers(extract, cross, jud):
    return Providers(extractor=FP([extract]), crosschecker=RoleFP(cross), judge=RoleFP(jud))

def _two_quote_extract():
    return json.dumps({"quotes":[
        {"text":"We will build 40,000 units by cutting permit timelines.","context":"h","issue":"housing",
         "is_own_words":True,"is_primary_venue":True},
        {"text":"We will hire 250 civilian staff to free up officers.","context":"p","issue":"policing",
         "is_own_words":True,"is_primary_venue":True}]})

# Source text must contain BOTH quotes verbatim (normalized) so the verbatim
# gate passes for each — the two quotes stand in for the two windows a real
# multi-topic source page would carry.
_TWO_QUOTE_SRC = ("Bass campaign site: We will build 40,000 units by cutting permit "
                  "timelines. Policy page: We will hire 250 civilian staff to free "
                  "up officers.")

def test_run_source_identical_workers_1_vs_4():
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,"tag_ok":True})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9,"forward_looking":0.9})
    def run(w):
        items, leads = run_source(politician_id="p1", source_url="https://karenbass.com/x",
            cited_via=None, providers=_role_providers(_two_quote_extract(), cross, jud),
            fetcher=lambda u: _TWO_QUOTE_SRC, candidate_name="Karen Bass", batch_id="b1", max_workers=w)
        return [(i.issue, i.status) for i in items]
    assert run(1) == run(4)
    assert len(run(4)) == 2 and all(s==Status.GREEN.value for _,s in run(4))


def _two_quote_tsrc():
    q1 = "We will build 40,000 units by cutting permit timelines."
    q2 = "We will hire 250 civilian staff to free up officers."
    full = (f"Moderator: What will you do on housing?\nKaren Bass: {q1}\n"
            f"Moderator: And on policing?\nKaren Bass: {q2}")
    return TranscriptSource(meeting_id="m1", source_url="https://site/m1",
        video_url="https://youtu.be/x", title="Debate", event_kind="debate",
        full_text=full, segments=[(20.0, q1), (140.0, q2)])

def test_run_transcript_source_identical_workers_1_vs_4():
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,"tag_ok":True})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9,"forward_looking":0.9})
    def run(w):
        items, leads = run_transcript_source(_two_quote_tsrc(), politician_id="p1",
            providers=_role_providers(_two_quote_extract(), cross, jud),
            candidate_name="Karen Bass", batch_id="b1", max_workers=w)
        return [(i.issue, i.status) for i in items]
    assert run(1) == run(4)
    assert len(run(4)) == 2 and all(s==Status.GREEN.value for _,s in run(4))


def test_primary_quote_reciting_record_is_flagged_not_forward():
    # high tag/context/mechanism, low dispute — but the judge scores forward_looking
    # low (a past-record recitation, not a forward stance), so the item must FLAG.
    extract = json.dumps({"quotes": [{"text":"We will build 30,000 units of housing",
        "context": SRC, "issue":"housing","date":"2026","setting":"campaign site",
        "is_own_words":True,"is_primary_venue":True,"reported_event":None,
        "primary_handle":None}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,
                        "tag_ok":True,"issue":"housing","notes":""})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,
                      "mechanism":0.9,"forward_looking":0.1})
    items, leads = run_source(politician_id="p1",
        source_url="https://karenbass.com/housing", cited_via=None,
        providers=_providers(extract, cross, jud), fetcher=lambda u: SRC,
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.FLAGGED.value
    assert "judge:record-not-forward" in items[0].status_reasons
