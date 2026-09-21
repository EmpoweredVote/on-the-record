# Evidence Transcript Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the evidence pipeline read a candidate's ingested `meetings.*` transcripts and extract their spoken, own-words positions into `inform.evidence_items`, each with a click-to-seek deep link.

**Architecture:** A new read-only source function (`data.fetch_transcript_sources`) assembles each meeting's full speaker-labeled transcript for a candidate; a new pipeline path (`run_transcript_source`) runs it through the existing extract → verbatim → cross-check → judge → disposition steps with `source_type=PRIMARY` and a timestamped deep link; the runner gets a `--source web|transcripts|both` flag. Reuses the own-words extractor (PR #250) to pull only the candidate's statements out of the interleaved transcript.

**Tech Stack:** Python 3, psycopg2 (read-only), pytest. LLMs via OpenRouter.

## Global Constraints

- **Read-only DB.** This lane only reads `meetings.*` and `essentials`/`inform`; no writes here (the committer stays the only writer). No schema change; no ev-accounts change.
- **Own-words by construction, but still gated.** Transcript turns are the candidate speaking at the event, so `own_words`/`primary` are definitionally true; the substantive gate that still applies is `judge:mechanism` (a spoken answer can still be a bare goal) and the tag check. The verbatim gate still runs (the quote must be an exact substring of the transcript text).
- **Reuse, don't fork, the per-quote evaluation.** `run_source` and `run_transcript_source` must share one evaluation helper — do not duplicate the verify/cross-check/judge/disposition block.
- **`EvidenceItem` shape is unchanged** (see `src/evidence/models.py`): `source_type=SourceType.PRIMARY.value`, `deep_link` carries the timestamp, `context` = the eliciting question + turn.
- LLM models: extractor `haiku-or`, crosschecker `gemini-flash`, judge `deepseek` (OpenRouter).
- Run tests + runner with the MAIN checkout venv: `~/Documents/GitHub/on-the-record/.venv/bin/python`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `src/evidence/data.py` — ADD `TranscriptSource` dataclass + `fetch_transcript_sources(conn, politician_id)`.
- `src/evidence/pipeline.py` — REFACTOR the per-quote loop of `run_source` into `_evaluate_quote(...)`; ADD `run_transcript_source(...)`; extend `run_candidate` to accept transcript sources.
- `scripts/evidence_slice.py` — ADD `--source web|transcripts|both`; wire the transcript lane.
- `tests/test_evidence_data.py`, `tests/test_evidence_pipeline.py` — EXTEND.

---

### Task 1: `fetch_transcript_sources` + `TranscriptSource`

**Files:**
- Modify: `src/evidence/data.py`
- Test: `tests/test_evidence_data.py`

**Interfaces:**
- Produces: `TranscriptSource` (dataclass) and `fetch_transcript_sources(conn, politician_id) -> list[TranscriptSource]`. `run_transcript_source` (Task 3) consumes these.

`TranscriptSource` fields: `meeting_id: str`, `source_url: str`, `video_url: str | None`, `title: str | None`, `event_kind: str | None`, `full_text: str` (speaker-labeled interleaved transcript), `segments: list[tuple[float, str]]` (each `(start_time_seconds, text)` in order — used to locate a quote's timestamp).

- [ ] **Step 1: Write the failing test (mock connection)**

```python
# tests/test_evidence_data.py
from src.evidence.data import fetch_transcript_sources

class _Cur:
    """Minimal cursor: returns canned rows per-query by matching a keyword."""
    def __init__(self, speakers, segments):
        self._speakers, self._segments, self._last = speakers, segments, None
    def execute(self, sql, params=None):
        self._last = "speakers" if "from meetings.speakers" in sql.lower() and "segments" not in sql.lower() else "segments"
    def fetchall(self):
        return self._speakers if self._last == "speakers" else self._segments
    def __enter__(self): return self
    def __exit__(self, *a): return False

class _Conn:
    def __init__(self, speakers, segments): self._s, self._g = speakers, segments
    def cursor(self, *a, **k): return _Cur(self._s, self._g)

def test_fetch_transcript_sources_assembles_speaker_labeled_text():
    # one meeting; candidate speaker id = 10; a moderator turn then the candidate's turn
    speakers = [("m1", "Karen Bass", "https://site/m1", "https://youtu.be/x", "Debate", "debate")]
    segments = [
        (0, 12.0, "Moderator", "What will you do on housing?"),
        (1, 20.0, "Karen Bass", "We will build 40,000 units by cutting permit timelines."),
    ]
    src = fetch_transcript_sources(_Conn(speakers, segments), "p1")
    assert len(src) == 1
    s = src[0]
    assert s.meeting_id == "m1" and s.video_url == "https://youtu.be/x"
    assert "Moderator: What will you do on housing?" in s.full_text
    assert "Karen Bass: We will build 40,000 units" in s.full_text
    assert (20.0, "We will build 40,000 units by cutting permit timelines.") in s.segments
```

- [ ] **Step 2: Run it to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_data.py -k transcript -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement**

```python
# src/evidence/data.py  (add)
from dataclasses import dataclass

@dataclass
class TranscriptSource:
    meeting_id: str
    source_url: str
    video_url: "str | None"
    title: "str | None"
    event_kind: "str | None"
    full_text: str
    segments: list  # list[(start_time_seconds: float, text: str)] in order


def fetch_transcript_sources(conn, politician_id) -> list:
    """Assemble one TranscriptSource per meeting where this politician is a linked
    speaker: the full speaker-labeled transcript (so the own-words extractor can
    pull only their statements, with the eliciting question as context), plus the
    ordered (start_time, text) segments for timestamp lookup."""
    import psycopg2.extras
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT m.id, m.title, m.source_url, m.video_url, m.event_kind "
        "FROM meetings.speakers sp JOIN meetings.meetings m ON m.id = sp.meeting_id "
        "WHERE sp.politician_id = %s ORDER BY m.id", (politician_id,))
    meetings = cur.fetchall()
    out = []
    for mid, title, source_url, video_url, event_kind in meetings:
        cur2 = conn.cursor()
        cur2.execute(
            "SELECT segment_index, start_time, speaker_name, text "
            "FROM meetings.segments WHERE meeting_id = %s ORDER BY segment_index", (mid,))
        rows = cur2.fetchall()
        lines, segs = [], []
        for _idx, start, speaker, text in rows:
            text = (text or "").strip()
            if not text:
                continue
            lines.append(f"{speaker or 'Speaker'}: {text}")
            segs.append((float(start) if start is not None else 0.0, text))
        out.append(TranscriptSource(
            meeting_id=str(mid), source_url=source_url or "", video_url=video_url,
            title=title, event_kind=event_kind, full_text="\n".join(lines), segments=segs))
    return out
```

(The test's `_Cur` maps both `conn.cursor()` calls to canned rows; against the real DB, `conn.cursor()` returns real cursors. The two-cursor shape matches the test's per-query keyword match.)

- [ ] **Step 4: Run to verify pass**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_data.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/data.py tests/test_evidence_data.py
git commit -m "feat(evidence): fetch_transcript_sources reads a candidate's meetings turns

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Refactor the per-quote loop into `_evaluate_quote` (behavior-preserving)

**Files:**
- Modify: `src/evidence/pipeline.py`
- Test: `tests/test_evidence_pipeline.py`

**Interfaces:**
- Produces: `_evaluate_quote(cand, source_text, *, politician_id, source_url, cited_via, deep_link, source_type, providers, candidate_name, prov) -> EvidenceItem`. `run_source` and `run_transcript_source` (Task 3) both call it.

- [ ] **Step 1: Run the existing pipeline tests first (they are the regression guard)**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -v`
Expected: PASS (record the count; it must not change after the refactor).

- [ ] **Step 2: Extract the helper**

In `src/evidence/pipeline.py`, move the body that runs after `verbatim_ok` passes (the `crosscheck` → `judge_quote` → `GateResults` → `decide` → `EvidenceItem` construction) plus the `verbatim_ok`-fail branch into:

```python
def _evaluate_quote(cand, source_text, *, politician_id, source_url, cited_via,
                    deep_link, source_type, providers, candidate_name, prov) -> "EvidenceItem":
    if not verbatim_ok(cand.text, source_text):
        return EvidenceItem(politician_id=politician_id, issue=cand.issue,
            evidence_type="quote", verbatim_text=cand.text, source_url=source_url,
            cited_via=cited_via, context=cand.context, deep_link=deep_link,
            source_type=source_type, gates=GateResults(verbatim=False),
            status=Status.DROPPED.value, status_reasons=["verbatim-fail"], provenance=prov)
    cc = crosscheck(cand, source_text, candidate_name=candidate_name,
                    provider=providers.crosschecker)
    js = judge_quote(cand, provider=providers.judge)
    gates = GateResults(verbatim=True, own_words=cc.own_words, in_context=cc.in_context,
        primary=cc.primary, tag_agree=cc.tag_agree, judge_tag_ok=js.tag_ok,
        judge_context_sufficient=js.context_sufficient, judge_dispute_risk=js.dispute_risk,
        judge_mechanism=js.mechanism)
    status, reasons = decide(gates, source_type)
    return EvidenceItem(politician_id=politician_id, issue=cand.issue, evidence_type="quote",
        verbatim_text=cand.text, source_url=source_url, cited_via=cited_via,
        context=cand.context, deep_link=deep_link, source_type=source_type, gates=gates,
        status=status, status_reasons=reasons, provenance=prov)
```

Then rewrite `run_source`'s loop body to call `_evaluate_quote(cand, text, politician_id=politician_id, source_url=source_url, cited_via=cited_via, deep_link=source_url, source_type=<the source_type it already computes>, providers=providers, candidate_name=candidate_name, prov=prov)` for each non-lead `cand`. The lead branch (`not cand.is_primary_venue or cand.reported_event`) stays in `run_source`. Behavior must be identical.

- [ ] **Step 3: Run the existing pipeline tests — must be unchanged**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -v`
Expected: PASS, same count as Step 1 (green/flagged/dropped outcomes identical). If any differs, the refactor changed behavior — fix it, do not adjust the tests.

- [ ] **Step 4: Commit**

```bash
git add src/evidence/pipeline.py
git commit -m "refactor(evidence): share per-quote evaluation via _evaluate_quote (no behavior change)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `run_transcript_source` (+ timestamped deep link)

**Files:**
- Modify: `src/evidence/pipeline.py`
- Test: `tests/test_evidence_pipeline.py`

**Interfaces:**
- Consumes: `_evaluate_quote` (Task 2), `TranscriptSource` (Task 1).
- Produces: `run_transcript_source(source, *, politician_id, providers, candidate_name, batch_id) -> (list[EvidenceItem], list[Lead])` and `_deep_link(source, quote_text) -> str`. `run_candidate` gains a `transcript_sources=None` kwarg that routes each through `run_transcript_source`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evidence_pipeline.py
import json
from src.evidence.pipeline import run_transcript_source
from src.evidence.data import TranscriptSource
from src.evidence.models import Status, SourceType

def _tsrc():
    turn = "We will build 40,000 units by cutting permit timelines."
    full = f"Moderator: What will you do on housing?\nKaren Bass: {turn}"
    return TranscriptSource(meeting_id="m1", source_url="https://site/m1",
        video_url="https://youtu.be/x", title="Debate", event_kind="debate",
        full_text=full, segments=[(20.0, turn)])

def test_transcript_quote_is_green_primary_with_timestamp_deeplink():
    turn = "We will build 40,000 units by cutting permit timelines."
    extract = json.dumps({"quotes": [{"text": turn, "context": "housing question",
        "issue":"housing","is_own_words":True,"is_primary_venue":True}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,"tag_ok":True})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9})
    items, leads = run_transcript_source(_tsrc(), politician_id="p1",
        providers=_providers(extract, cross, jud), candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.GREEN.value
    assert items[0].source_type == SourceType.PRIMARY.value
    assert items[0].deep_link.startswith("https://youtu.be/x") and "20" in items[0].deep_link

def test_transcript_reworded_quote_drops_verbatim_fail():
    extract = json.dumps({"quotes": [{"text":"As mayor she plans to construct homes.",
        "context":"x","issue":"housing","is_own_words":True,"is_primary_venue":True}]})
    items, leads = run_transcript_source(_tsrc(), politician_id="p1",
        providers=_providers(extract, "{}", "{}"), candidate_name="Karen Bass", batch_id="b1")
    assert items[0].status == Status.DROPPED.value and "verbatim-fail" in items[0].status_reasons
```

(`_providers` already exists in this test file.)

- [ ] **Step 2: Run to verify fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -k transcript -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement**

```python
# src/evidence/pipeline.py
from .data import TranscriptSource  # if not already importable without a cycle; else import locally

def _deep_link(source, quote_text: str) -> str:
    """Point at the transcript segment the quote starts in: <video_url>#t=<seconds>.
    Falls back to the video/source URL with no timestamp when no segment matches."""
    base = source.video_url or source.source_url or ""
    head = (quote_text or "").strip()[:40]
    for start, text in source.segments:
        if head and head in text:
            sep = "&t=" if ("youtube.com" in base or "youtu.be" in base) else "#t="
            return f"{base}{sep}{int(start)}{'s' if 'youtu' in base else ''}"
    return base

def run_transcript_source(source, *, politician_id, providers, candidate_name, batch_id):
    prov = {"extractor": getattr(providers.extractor, "model", "extractor"),
            "crosschecker": getattr(providers.crosschecker, "model", "crosschecker"),
            "judge": getattr(providers.judge, "model", "judge"), "batch": batch_id}
    items = []
    for cand in extract_quotes(source.full_text, candidate_name=candidate_name,
                               provider=providers.extractor):
        items.append(_evaluate_quote(cand, source.full_text, politician_id=politician_id,
            source_url=source.source_url, cited_via=source.meeting_id,
            deep_link=_deep_link(source, cand.text), source_type=SourceType.PRIMARY.value,
            providers=providers, candidate_name=candidate_name, prov=prov))
    return items, []
```

Extend `run_candidate` with `transcript_sources=None`: after the web-source loop, `for ts in (transcript_sources or []): it, ld = run_transcript_source(ts, politician_id=politician_id, providers=providers, candidate_name=candidate_name, batch_id=batch_id); all_items += it; all_leads += ld`.

- [ ] **Step 4: Run to verify pass + the whole pipeline file**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -v`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add src/evidence/pipeline.py tests/test_evidence_pipeline.py
git commit -m "feat(evidence): run_transcript_source extracts spoken evidence with click-to-seek deep links

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Runner `--source web|transcripts|both`

**Files:**
- Modify: `scripts/evidence_slice.py`
- Test: `tests/test_evidence_slice.py` (create if absent; else extend)

**Interfaces:** Consumes `fetch_transcript_sources` (Task 1) and `run_candidate`'s `transcript_sources` (Task 3).

- [ ] **Step 1: Write the failing test (parser routing)**

```python
# tests/test_evidence_slice.py
import scripts.evidence_slice as s

def test_source_flag_defaults_both_and_parses():
    assert s.build_parser().parse_args([]).source == "both"
    assert s.build_parser().parse_args(["--source","transcripts"]).source == "transcripts"
```

- [ ] **Step 2: Run to verify fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_slice.py -v`
Expected: FAIL (no `source` arg).

- [ ] **Step 3: Implement**

In `build_parser`: `ap.add_argument("--source", choices=["web","transcripts","both"], default="both")`.
In `main`, per candidate: build `sources = data.fetch_cited_sources(conn, cand["politician_id"])` only when `args.source in ("web","both")` (else `[]`); build `tsrc = data.fetch_transcript_sources(conn, cand["politician_id"])` only when `args.source in ("transcripts","both")` (else `[]`); pass both to `run_candidate(..., sources=sources, transcript_sources=tsrc)`. Keep `--limit` applying to web sources as before.

- [ ] **Step 4: Run to verify pass + import check**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_slice.py -v && ~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/ -k evidence -q`
Expected: PASS; full evidence suite green.

- [ ] **Step 5: Commit**

```bash
git add scripts/evidence_slice.py tests/test_evidence_slice.py
git commit -m "feat(evidence): evidence_slice --source web|transcripts|both

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Human-gated validation — Bass + Raman transcripts (artifacts only)

**This task is not TDD; it spends LLM + network and needs Chris. Do NOT run automatically.**

- [ ] **Step 1: Run the transcript lane for both candidates**

```bash
export OPENROUTER_API_KEY=$(grep -E '^OPENROUTER_API_KEY=' ~/Documents/GitHub/on-the-record/.env.local | head -1 | cut -d= -f2- | tr -d '"'"'"'\r')
for pid in 21c9e711-fb18-4afb-884f-08acd2b598ba 26dbe16a-9dff-42c0-939f-5b5e529063ca; do
  ~/Documents/GitHub/on-the-record/.venv/bin/python scripts/evidence_slice.py \
    --candidate $pid --source transcripts \
    --env-file ~/Documents/GitHub/ev-accounts/backend/.env \
    --out docs/superpowers/spikes/2026-09-19-evidence-trust-core/transcripts-$pid
done
```

- [ ] **Step 2: Report to Chris**

From each `evidence_items.json`: green/flagged/dropped counts, that greens are genuinely the candidate's spoken words, and spot-check that several `deep_link`s open the right moment (`#t=`/`&t=`) in the video. Report per-candidate before/after context (transcripts are a NEW source; the web-lane rows are untouched). Chris decides whether to commit to prod.

---

### Task 6: Cost — feed only the candidate's turns (+ eliciting question)

**Files:**
- Modify: `src/evidence/data.py` (`fetch_transcript_sources`)
- Test: `tests/test_evidence_data.py`

**Why:** the base version feeds every speaker's turns to the extractor; the candidate is only ~50% of a debate and far less of a council meeting. Feed only their turns, each preceded by its eliciting (immediately prior, different-speaker) turn for context.

- [ ] **Step 1: Update the existing transcript test + add an exclusion test**

The mock now needs `speaker_id` on segment rows and a candidate-speaker-ids query. Update the fixture so segments carry `speaker_id`, the candidate's speaker id is known, and assert: (a) the candidate's turn is in `full_text`, (b) its immediately-preceding moderator turn is in `full_text` (question context), (c) a NON-adjacent other-speaker turn is NOT in `full_text`, (d) `segments` contains only the candidate's turns.

```python
# tests/test_evidence_data.py  — replace the transcript-assembly test body to match the new mock shape
class _Cur2:
    """Returns speaker-id rows for the speakers-in-meeting query, else segment rows."""
    def __init__(self, meetings, cand_ids, segments):
        self._meetings, self._cand_ids, self._segments, self._last = meetings, cand_ids, segments, None
    def execute(self, sql, params=None):
        s = sql.lower()
        if "from meetings.speakers" in s and "meetings.meetings" in s: self._last = "meetings"
        elif "from meetings.speakers" in s: self._last = "cand_ids"
        else: self._last = "segments"
    def fetchall(self):
        return {"meetings": self._meetings, "cand_ids": self._cand_ids, "segments": self._segments}[self._last]
    def __enter__(self): return self
    def __exit__(self, *a): return False
class _Conn2:
    def __init__(self, m, c, s): self._m, self._c, self._s = m, c, s
    def cursor(self, *a, **k): return _Cur2(self._m, self._c, self._s)

def test_fetch_transcript_sources_candidate_turns_plus_question_only():
    from src.evidence.data import fetch_transcript_sources
    meetings = [("m1", "Debate", "https://site/m1", "https://youtu.be/x", "debate")]
    cand_ids = [(10,)]  # candidate's speaker id in this meeting
    segments = [  # (segment_index, start_time, speaker_id, speaker_name, text)
        (0, 12.0, 99, "Moderator", "What will you do on housing?"),
        (1, 20.0, 10, "Karen Bass", "We will build 40,000 units by cutting permit timelines."),
        (2, 40.0, 88, "Opponent",  "I disagree with that approach entirely."),
    ]
    s = fetch_transcript_sources(_Conn2(meetings, cand_ids, segments), "p1")[0]
    assert "Karen Bass: We will build 40,000 units" in s.full_text     # candidate turn
    assert "Moderator: What will you do on housing?" in s.full_text    # eliciting question kept
    assert "Opponent" not in s.full_text                               # other speaker dropped
    assert s.segments == [(20.0, "We will build 40,000 units by cutting permit timelines.")]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_data.py -k transcript -v`
Expected: FAIL (current code includes all speakers + no speaker_id).

- [ ] **Step 3: Implement**

```python
def fetch_transcript_sources(conn, politician_id) -> list:
    """One TranscriptSource per meeting where this politician is a linked speaker:
    the candidate's OWN turns, each preceded by its eliciting (immediately prior,
    different-speaker) turn for context — not every speaker — plus the candidate's
    ordered (start_time, text) segments for timestamp lookup."""
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT m.id, m.title, m.source_url, m.video_url, m.event_kind "
        "FROM meetings.speakers sp JOIN meetings.meetings m ON m.id = sp.meeting_id "
        "WHERE sp.politician_id = %s ORDER BY m.id", (politician_id,))
    meetings = cur.fetchall()
    out = []
    for mid, title, source_url, video_url, event_kind in meetings:
        cur.execute("SELECT id FROM meetings.speakers WHERE meeting_id = %s AND politician_id = %s",
                    (mid, politician_id))
        cand_ids = {r[0] for r in cur.fetchall()}
        cur.execute(
            "SELECT segment_index, start_time, speaker_id, speaker_name, text "
            "FROM meetings.segments WHERE meeting_id = %s ORDER BY segment_index", (mid,))
        rows = cur.fetchall()
        lines, segs = [], []
        for i, (_idx, start, sid, speaker, text) in enumerate(rows):
            text = (text or "").strip()
            if not text or sid not in cand_ids:
                continue
            prev = rows[i - 1] if i > 0 else None
            if prev is not None and prev[2] not in cand_ids and (prev[4] or "").strip():
                lines.append(f"{prev[3] or 'Speaker'}: {(prev[4] or '').strip()}")  # eliciting question
            lines.append(f"{speaker or 'Speaker'}: {text}")
            segs.append((float(start) if start is not None else 0.0, text))
        out.append(TranscriptSource(
            meeting_id=str(mid), source_url=source_url or "", video_url=video_url,
            title=title, event_kind=event_kind, full_text="\n".join(lines), segments=segs))
    return out
```

(The `_Cur2` mock maps the three queries by keyword; the real DB returns real cursors. The split-speaker dedup regression test from the fix round still holds — the meetings query is unchanged.)

- [ ] **Step 4: Run to verify pass**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_data.py -v` then `tests/ -k evidence -q`
Expected: PASS (including the split-speaker dedup regression test).

- [ ] **Step 5: Commit**

```bash
git add src/evidence/data.py tests/test_evidence_data.py
git commit -m "perf(evidence): transcript lane feeds only the candidate's turns + eliciting question

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Cost — trim the cross-check's input to the quote's local window

**Files:**
- Modify: `src/evidence/pipeline.py`
- Test: `tests/test_evidence_pipeline.py`

**Why:** the cross-check re-sends up to 60K chars of transcript per quote (170 quotes → ~10M chars for Bass). Keep the independent check but feed it only a local window around the quote.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evidence_pipeline.py
def test_transcript_crosscheck_sees_trimmed_window_not_full_text():
    turn = "We will build 40,000 units by cutting permit timelines."
    big = ("UNRELATED FILLER. " * 5000) + f"Karen Bass: {turn} " + ("MORE FILLER. " * 5000)
    src = TranscriptSource(meeting_id="m1", source_url="https://site/m1",
        video_url="https://youtu.be/x", title="Debate", event_kind="debate",
        full_text=big, segments=[(20.0, turn)])
    extract = json.dumps({"quotes":[{"text":turn,"context":"housing","issue":"housing",
        "is_own_words":True,"is_primary_venue":True}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,"tag_ok":True})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9})
    prov = _providers(extract, cross, jud)
    items, _ = run_transcript_source(src, politician_id="p1", providers=prov,
                                     candidate_name="Karen Bass", batch_id="b1")
    # the crosschecker prompt it saw must contain the quote but be far smaller than full_text
    seen = prov.crosschecker.prompts[0]
    assert turn in seen and len(seen) < 4000 and len(seen) < len(big) // 5
    assert items[0].status == Status.GREEN.value
```

(Give the test file's `FP` fake provider a `self.prompts=[]` that records each `prompt` if it doesn't already, and make `_providers` expose `.crosschecker`. If `FP` already records prompts, reuse it.)

- [ ] **Step 2: Run to verify fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -k trimmed -v`
Expected: FAIL (crosscheck currently gets full_text).

- [ ] **Step 3: Implement**

Add `crosscheck_text=None` to `_evaluate_quote` and use it for the cross-check only (verbatim still uses `source_text`):

```python
def _evaluate_quote(cand, source_text, *, politician_id, source_url, cited_via,
                    deep_link, source_type, providers, candidate_name, prov,
                    crosscheck_text=None) -> "EvidenceItem":
    if not verbatim_ok(cand.text, source_text):
        ...  # unchanged verbatim-fail branch
    cc = crosscheck(cand, crosscheck_text if crosscheck_text is not None else source_text,
                    candidate_name=candidate_name, provider=providers.crosschecker)
    ...  # unchanged remainder
```

Add a local-window helper and use it in `run_transcript_source`:

```python
def _local_window(full_text: str, quote_text: str, radius: int = 800) -> str:
    head = (quote_text or "").strip()[:40]
    i = full_text.find(head) if head else -1
    if i < 0:
        return full_text[:2 * radius]
    return full_text[max(0, i - radius): i + len(quote_text) + radius]
```

In `run_transcript_source`, pass `crosscheck_text=_local_window(source.full_text, cand.text)` into `_evaluate_quote`. `run_source` is unchanged (passes no `crosscheck_text`, so the web lane still cross-checks against the full page).

- [ ] **Step 4: Run the pipeline tests**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -v` then `tests/ -k evidence -q`
Expected: PASS (existing web-lane tests unchanged; new trim test passes).

- [ ] **Step 5: Commit**

```bash
git add src/evidence/pipeline.py tests/test_evidence_pipeline.py
git commit -m "perf(evidence): cross-check transcripts against the quote's local window, not 60K

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Definitional primary/own-words for transcripts (recover false "not primary" flags)

**Files:**
- Modify: `src/evidence/pipeline.py` (`_evaluate_quote`, `run_transcript_source`)
- Test: `tests/test_evidence_pipeline.py`

**Why:** the trimmed cross-check window can't tell the source is the candidate's own event, so it answers `primary=false` on their own speech and false-flags ~half the greens (offline sim: Bass green 10→28, Raman 6→25). For a transcript, own-words and primary are true by construction. Keep the cross-check for `in_context` + `tag`; take own-words/primary as definitional.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_pipeline.py
def test_transcript_definitional_primary_greens_despite_crosscheck_primary_false():
    turn = "We will build 40,000 units by cutting permit timelines."
    src = TranscriptSource(meeting_id="m1", source_url="https://site/m1",
        video_url="https://youtu.be/x", title="Debate", event_kind="debate",
        full_text=f"Karen Bass: {turn}", segments=[(20.0, turn)])
    extract = json.dumps({"quotes":[{"text":turn,"context":"housing","issue":"housing",
        "is_own_words":True,"is_primary_venue":True}]})
    # cross-checker says NOT primary and NOT own_words (the window-starved failure), but in_context/tag ok
    cross = json.dumps({"own_words":False,"in_context":True,"primary":False,"tag_ok":True})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9})
    items,_ = run_transcript_source(src, politician_id="p1",
        providers=_providers(extract, cross, jud), candidate_name="Karen Bass", batch_id="b1")
    assert items[0].status == Status.GREEN.value          # definitional primary/own_words override
    assert items[0].gates.primary is True and items[0].gates.own_words is True
    assert items[0].gates.in_context is True              # crosscheck's in_context still used
```

- [ ] **Step 2: Run to verify fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -k definitional -v`
Expected: FAIL (currently uses cc.primary=False → flagged).

- [ ] **Step 3: Implement**

Add `definitional_primary=False` to `_evaluate_quote`; when set, override own-words/primary to True (keep the cross-check's in_context/tag):

```python
def _evaluate_quote(cand, source_text, *, politician_id, source_url, cited_via,
                    deep_link, source_type, providers, candidate_name, prov,
                    crosscheck_text=None, definitional_primary=False) -> "EvidenceItem":
    if not verbatim_ok(cand.text, source_text):
        ...  # unchanged verbatim-fail branch
    cc = crosscheck(cand, crosscheck_text if crosscheck_text is not None else source_text,
                    candidate_name=candidate_name, provider=providers.crosschecker)
    own_words = True if definitional_primary else cc.own_words
    primary = True if definitional_primary else cc.primary
    gates = GateResults(verbatim=True, own_words=own_words, in_context=cc.in_context,
        primary=primary, tag_agree=cc.tag_agree, judge_tag_ok=js.tag_ok, ...)  # rest unchanged
    ...
```

In `run_transcript_source`, pass `definitional_primary=True` into `_evaluate_quote`. `run_source` (web lane) does not pass it → unchanged.

- [ ] **Step 4: Run the pipeline tests**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -v` then `tests/ -k evidence -q`
Expected: PASS (web-lane tests unchanged; new definitional test passes).

- [ ] **Step 5: Commit**

```bash
git add src/evidence/pipeline.py tests/test_evidence_pipeline.py
git commit -m "fix(evidence): own-words/primary are definitional for transcripts (recover false not-primary flags)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** transcript source read (Task 1) ✓; transcript pipeline path with PRIMARY + verbatim gate + mechanism (Tasks 2–3) ✓; click-to-seek deep link (Task 3 `_deep_link`) ✓; question-as-context via full speaker-labeled transcript fed to the own-words extractor (Task 1 `full_text` + Task 3) ✓; runner `--source` (Task 4) ✓; artifact-based validation (Task 5) ✓; no schema/ev-accounts change ✓; shared evaluation (no fork) via the Task 2 refactor ✓.

**Placeholder scan:** none — SQL, dataclass, helper, and tests are concrete. The deep-link timestamp format (`#t=`/`&t=`) is a concrete default; Task 5 spot-checks it resolves and the plan says to match the app convention if it differs.

**Type consistency:** `TranscriptSource` fields used by `run_transcript_source`/`_deep_link` match Task 1; `_evaluate_quote` signature is used identically by `run_source` and `run_transcript_source`; `run_candidate`'s new `transcript_sources` kwarg matches the runner call.

**Honesty:** Task 2 is a behavior-preserving refactor guarded by the existing pipeline tests (not red-green); own_words/primary being definitionally true for transcripts is stated, and the real gate (verbatim + mechanism) is exercised by Task 3's tests.

## Execution Handoff

1. **Subagent-Driven (recommended)** — fresh subagent per task, review after each, final review.
2. **Inline Execution** — with checkpoints.
