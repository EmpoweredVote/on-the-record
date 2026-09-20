# Evidence Trust Core (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, with numbers, that a multi-agent pipeline can turn the LA Mayor candidates' already-cited sources into verbatim, primary-source, context-carrying quote evidence — emitting chase-the-primary leads — with an eval harness and no DB writes.

**Architecture:** A new pure-where-possible `src/evidence/` package: deterministic gates (verbatim, domain triage, disposition, eval scorer) + three LLM roles (extractor, independent cross-checker, judge) wired by a `pipeline` orchestrator, driven by a manual online runner `scripts/evidence_slice.py`. Output is artifacts only (`evidence_items.json`, `leads.json`, `eval_report.md`, `review.html`). Input is read-only from the ev-accounts `inform`/`essentials` DB.

**Tech Stack:** Python 3.14 (on-the-record `.venv`), psycopg2 (read-only DB), pytest (offline unit tests), OpenRouter via `src.llm_providers`, reuse of `src.discovery.feeds` + `src.source_key`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-19-evidence-trust-core-slice-design.md`. Principles: `docs/quote-curation/PRINCIPLES.md`; editing discipline: `.claude/skills/publish-quotes/EDITORIAL.md`.
- **No DB writes, no migration.** ev-accounts is read-only. Every DB call is `SET SESSION` read-only / autocommit.
- **Scope:** LA Mayor `race_id = 9e888818-c50b-4c61-a106-a0839ff2479d` (Bass `21c9e711-fb18-4afb-884f-08acd2b598ba`, Raman `26dbe16a-9dff-42c0-939f-5b5e529063ca`). **Verbatim quotes only.** No votes/actions, no video transcription, no new ingestion.
- **A green item comes only from a `primary` source.** Verbatim quotes reported by a secondary source are never green — they become leads carrying the reported text marked `"reported by <secondary>; NOT verified against a primary — chase to confirm"`.
- **Verbatim gate is deterministic and mandatory:** an emitted quote must be an ellipsis-tolerant normalized substring of the fetched source text, or it is dropped.
- **Model independence:** extractor, cross-checker, judge are different `src.llm_providers` model keys. Defaults: extractor `sonnet`, cross-checker `gemini-flash`, judge `gpt5-mini` (all overridable by runner flags). LLM traffic via OpenRouter per the `openrouter-llm-migration` policy.
- **Run everything with the main checkout venv:** `~/Documents/GitHub/on-the-record/.venv/bin/python` (the worktree has no venv). Online runs source keys from the main checkout `.env.local` (contains `OPENROUTER_API_KEY`) or `--env-file`.
- **Commit trailer:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Offline unit tests never hit the network or the live DB (inject a `FakeProvider` and a fake fetcher). Only `scripts/evidence_slice.py` and `src/evidence/data.py` touch the network/DB, behind the manual runner.

## File Structure

Created under `src/evidence/`:

- `models.py` — dataclasses + enums: `SourceType`, `Status`, `GateResults`, `QuoteCandidate`, `CrossCheckVerdict`, `JudgeScores`, `EvidenceItem`, `Lead`. Pure.
- `verify.py` — `normalize`, `verbatim_ok` (ellipsis-tolerant substring). Pure, deterministic.
- `triage.py` — `classify_domain(url)` deterministic domain routing. Pure.
- `disposition.py` — thresholds + `decide(gates, source_type)` → `(status, reasons)`. Pure.
- `extract.py` — `build_extract_prompt`, `parse_extract`, `extract_quotes(text, *, candidate_name, provider)`. LLM (provider injected).
- `crosscheck.py` — `build_crosscheck_prompt`, `parse_crosscheck`, `crosscheck(cand, source_text, *, candidate_name, provider)`. LLM.
- `judge.py` — `build_judge_prompt`, `parse_judge`, `judge(item, *, provider)`. LLM.
- `leads.py` — `to_lead(...)`. Pure.
- `eval.py` — `Metrics`, `score(items, leads, gold)`. Pure.
- `report.py` — `render_report(metrics)`, `render_review_html(items, leads)`. Pure.
- `data.py` — read-only DB: `database_url`, `fetch_roster`, `fetch_cited_sources`, `load_topic_keys`. Thin.
- `pipeline.py` — `run_candidate(...)`, `run_source(...)` orchestration; providers + fetcher injected.

Runner: `scripts/evidence_slice.py`. Gold template + README + outputs: `docs/superpowers/spikes/2026-09-19-evidence-trust-core/`.

Tests: `tests/test_evidence_verify.py`, `test_evidence_triage.py`, `test_evidence_disposition.py`, `test_evidence_extract.py`, `test_evidence_crosscheck.py`, `test_evidence_judge.py`, `test_evidence_leads.py`, `test_evidence_eval.py`, `test_evidence_report.py`, `test_evidence_pipeline.py`.

---

### Task 1: Models (the atom)

**Files:**
- Create: `src/evidence/__init__.py` (empty)
- Create: `src/evidence/models.py`
- Test: `tests/test_evidence_models.py`

**Interfaces:**
- Produces: `SourceType`, `Status` (str enums); dataclasses `GateResults`, `QuoteCandidate`, `CrossCheckVerdict`, `JudgeScores`, `EvidenceItem`, `Lead`; `EvidenceItem.to_json()` / `Lead.to_json()` returning plain dicts.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_models.py
from src.evidence.models import (SourceType, Status, GateResults, EvidenceItem, Lead)

def test_evidence_item_to_json_roundtrips_enums_and_gates():
    item = EvidenceItem(
        politician_id="p1", issue="housing", evidence_type="quote",
        verbatim_text="We will build.", source_url="https://ex.com/a",
        cited_via="https://ontheissues.org/x", context="…We will build…",
        deep_link="https://ex.com/a", source_type=SourceType.PRIMARY,
        gates=GateResults(verbatim=True, own_words=True, in_context=True,
                          primary=True, tag_agree=True, judge_tag_ok=0.9,
                          judge_context_sufficient=0.9, judge_dispute_risk=0.1),
        status=Status.GREEN, status_reasons=[], provenance={"batch": "b1"})
    j = item.to_json()
    assert j["source_type"] == "primary" and j["status"] == "green"
    assert j["gates"]["verbatim"] is True and j["issue"] == "housing"

def test_lead_defaults_carry_the_unverified_marker():
    lead = Lead(politician_id="p1", reported_text="I said X at the debate.",
                issue="crime", event="KABC debate, 2026-05-01",
                secondary_url="https://news.com/story", primary_handle=None)
    assert "NOT verified against a primary" in lead.note
    assert lead.to_json()["reported_text"] == "I said X at the debate."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_models.py -v`
Expected: FAIL with `ModuleNotFoundError: src.evidence.models`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/models.py
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class SourceType(str, Enum):
    PRIMARY = "primary"
    POINTER = "pointer"
    SECONDARY_LEAD = "secondary_lead"
    SCORECARD_QUIZ = "scorecard_quiz"
    VOTE_RECORD = "vote_record"
    VIDEO_UNFETCHED = "video_unfetched"
    DEAD = "dead"


class Status(str, Enum):
    GREEN = "green"
    FLAGGED = "flagged"
    DROPPED = "dropped"


@dataclass
class GateResults:
    verbatim: bool
    own_words: Optional[bool] = None
    in_context: Optional[bool] = None
    primary: Optional[bool] = None
    tag_agree: Optional[bool] = None
    judge_tag_ok: Optional[float] = None
    judge_context_sufficient: Optional[float] = None
    judge_dispute_risk: Optional[float] = None


@dataclass
class QuoteCandidate:
    text: str
    context: str
    issue: str
    date: Optional[str] = None
    setting: Optional[str] = None
    is_own_words: bool = True
    is_primary_venue: bool = True
    reported_event: Optional[str] = None   # set when quote is reported from a spoken event
    primary_handle: Optional[str] = None


@dataclass
class CrossCheckVerdict:
    own_words: bool
    in_context: bool
    primary: bool
    tag_agree: bool
    issue: Optional[str] = None
    notes: str = ""


@dataclass
class JudgeScores:
    tag_ok: float
    context_sufficient: float
    dispute_risk: float
    notes: str = ""


def _enumval(v):
    return v.value if isinstance(v, Enum) else v


@dataclass
class EvidenceItem:
    politician_id: str
    issue: str
    evidence_type: str
    verbatim_text: str
    source_url: str
    cited_via: Optional[str]
    context: str
    deep_link: str
    source_type: str
    gates: GateResults
    status: str
    status_reasons: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        d["source_type"] = _enumval(self.source_type)
        d["status"] = _enumval(self.status)
        return d


@dataclass
class Lead:
    politician_id: str
    reported_text: str
    issue: str
    event: str
    secondary_url: str
    primary_handle: Optional[str]
    note: str = "reported by secondary; NOT verified against a primary — chase to confirm"

    def to_json(self) -> dict:
        return asdict(self)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/__init__.py src/evidence/models.py tests/test_evidence_models.py
git commit -m "feat(evidence): evidence-item + lead models for the trust-core slice

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Deterministic verbatim gate

**Files:**
- Create: `src/evidence/verify.py`
- Test: `tests/test_evidence_verify.py`

**Interfaces:**
- Produces: `normalize(s: str) -> str`; `verbatim_ok(quote: str, source_text: str) -> bool` (ellipsis-tolerant: each contiguous run between `...`/`…` must appear in order in the source).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_verify.py
from src.evidence.verify import normalize, verbatim_ok

SRC = ("Mayor Bass said, “We will build 30,000 units of housing, "
       "and we will do it with union labor,” during the forum.")

def test_exact_substring_passes():
    assert verbatim_ok("We will build 30,000 units of housing", SRC)

def test_curly_and_straight_quotes_normalize_equal():
    assert verbatim_ok('"We will build 30,000 units of housing"', SRC)

def test_ellipsis_tolerant_runs_must_be_in_order():
    assert verbatim_ok("We will build 30,000 units of housing … with union labor", SRC)
    assert not verbatim_ok("with union labor … We will build 30,000 units", SRC)

def test_non_substring_fails():
    assert not verbatim_ok("We will build affordable homes for everyone", SRC)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_verify.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/verify.py
from __future__ import annotations
import re
import unicodedata

_WS = re.compile(r"\s+")
_TRANS = {0x2019: "'", 0x2018: "'", 0x201c: '"', 0x201d: '"',
          0x2013: "-", 0x2014: "-"}


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").translate(_TRANS)
    s = s.replace("…", "...")
    return _WS.sub(" ", s).strip().lower()


def verbatim_ok(quote: str, source_text: str) -> bool:
    q = normalize(quote)
    parts = [p for p in (seg.strip() for seg in q.split("...")) if p]
    if not parts:
        return False
    hay = normalize(source_text)
    pos = 0
    for p in parts:
        i = hay.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_verify.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/verify.py tests/test_evidence_verify.py
git commit -m "feat(evidence): deterministic ellipsis-tolerant verbatim gate

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Deterministic domain triage

**Files:**
- Create: `src/evidence/triage.py`
- Test: `tests/test_evidence_triage.py`

**Interfaces:**
- Consumes: `SourceType` (Task 1); `src.source_key` for URL parsing patterns (reference only).
- Produces: `registrable_domain(url: str) -> str`; `classify_domain(url: str) -> SourceType | None` — returns a `SourceType` for a domain with a known rule, else `None` meaning "an ordinary web page — the LLM decides primary vs secondary."

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_triage.py
from src.evidence.models import SourceType
from src.evidence.triage import registrable_domain, classify_domain

def test_domain_rules():
    assert classify_domain("https://www.lcv.org/scorecard") is SourceType.SCORECARD_QUIZ
    assert classify_domain("https://isidewith.com/x") is SourceType.SCORECARD_QUIZ
    assert classify_domain("https://youtu.be/abc") is SourceType.VIDEO_UNFETCHED
    assert classify_domain("https://www.congress.gov/bill/1") is SourceType.VOTE_RECORD
    assert classify_domain("https://cityclerk.lacity.org/x") is SourceType.VOTE_RECORD
    assert classify_domain("https://en.wikipedia.org/wiki/Karen_Bass") is SourceType.POINTER
    assert classify_domain("https://www.ontheissues.org/x") is SourceType.POINTER

def test_ordinary_web_page_returns_none_for_llm():
    assert classify_domain("https://nithyaforthecity.com/housing") is None
    assert classify_domain("https://laist.com/news/story") is None

def test_registrable_domain_strips_www():
    assert registrable_domain("https://www.LAist.com/a") == "laist.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_triage.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/triage.py
from __future__ import annotations
from urllib.parse import urlsplit
from .models import SourceType

SCORECARD_QUIZ_DOMAINS = {"lcv.org", "isidewith.com", "votesmart.org",
                          "justfacts.votesmart.org"}
VIDEO_DOMAINS = {"youtube.com", "youtu.be", "m.youtube.com", "vimeo.com"}
POINTER_DOMAINS = {"ontheissues.org", "en.wikipedia.org", "wikipedia.org",
                   "web.archive.org"}
# Primary-for-VOTES (out of this slice; logged, not extracted).
VOTE_RECORD_DOMAINS = {
    "congress.gov", "govinfo.gov", "cityclerk.lacity.org",
    "leginfo.legislature.ca.gov", "mgaleg.maryland.gov",
    "legislature.maine.gov", "malegislature.gov", "capitol.texas.gov",
    "senate.gov", "senate.texas.gov", "ncleg.gov", "azleg.gov",
    "docs.legis.wisconsin.gov", "olis.oregonlegislature.gov",
    "lawfilesext.leg.wa.gov", "legacylis.virginia.gov",
}


def registrable_domain(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def classify_domain(url: str) -> "SourceType | None":
    d = registrable_domain(url)
    if d in SCORECARD_QUIZ_DOMAINS:
        return SourceType.SCORECARD_QUIZ
    if d in VIDEO_DOMAINS:
        return SourceType.VIDEO_UNFETCHED
    if d in VOTE_RECORD_DOMAINS:
        return SourceType.VOTE_RECORD
    if d in POINTER_DOMAINS:
        return SourceType.POINTER
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_triage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/triage.py tests/test_evidence_triage.py
git commit -m "feat(evidence): deterministic domain triage (pointer/scorecard/vote/video)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Disposition (green / flagged / dropped)

**Files:**
- Create: `src/evidence/disposition.py`
- Test: `tests/test_evidence_disposition.py`

**Interfaces:**
- Consumes: `GateResults`, `SourceType` (Task 1).
- Produces: thresholds `TAG_MIN=0.7`, `CONTEXT_MIN=0.7`, `DISPUTE_MAX=0.3`; `decide(gates: GateResults, source_type) -> tuple[str, list[str]]` returning a `Status` value string + reasons.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_disposition.py
from src.evidence.models import GateResults, SourceType, Status
from src.evidence.disposition import decide

def _full(**kw):
    base = dict(verbatim=True, own_words=True, in_context=True, primary=True,
                tag_agree=True, judge_tag_ok=0.9, judge_context_sufficient=0.9,
                judge_dispute_risk=0.1)
    base.update(kw)
    return GateResults(**base)

def test_all_pass_primary_is_green():
    status, reasons = decide(_full(), SourceType.PRIMARY)
    assert status == Status.GREEN.value and reasons == []

def test_verbatim_fail_drops():
    status, reasons = decide(_full(verbatim=False), SourceType.PRIMARY)
    assert status == Status.DROPPED.value and "verbatim-fail" in reasons

def test_scorecard_drops_even_if_verbatim():
    status, reasons = decide(_full(), SourceType.SCORECARD_QUIZ)
    assert status == Status.DROPPED.value

def test_non_primary_flags():
    status, reasons = decide(_full(primary=False), SourceType.SECONDARY_LEAD)
    assert status == Status.FLAGGED.value and "not-primary" in reasons

def test_crosscheck_disagreement_flags():
    status, reasons = decide(_full(own_words=False), SourceType.PRIMARY)
    assert status == Status.FLAGGED.value and "crosscheck:own_words" in reasons

def test_high_dispute_risk_flags():
    status, reasons = decide(_full(judge_dispute_risk=0.8), SourceType.PRIMARY)
    assert status == Status.FLAGGED.value and "judge:dispute-risk" in reasons
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_disposition.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/disposition.py
from __future__ import annotations
from .models import GateResults, SourceType, Status

TAG_MIN = 0.7
CONTEXT_MIN = 0.7
DISPUTE_MAX = 0.3

_DROP_TYPES = {SourceType.SCORECARD_QUIZ.value, SourceType.DEAD.value}


def _tv(v):
    return v.value if isinstance(v, SourceType) else v


def decide(gates: GateResults, source_type) -> tuple[str, list]:
    st = _tv(source_type)
    if not gates.verbatim:
        return Status.DROPPED.value, ["verbatim-fail"]
    if st in _DROP_TYPES:
        return Status.DROPPED.value, [st]
    reasons: list = []
    if st != SourceType.PRIMARY.value:
        reasons.append("not-primary")
    for name, val in (("own_words", gates.own_words), ("in_context", gates.in_context),
                      ("primary", gates.primary), ("tag_agree", gates.tag_agree)):
        if val is False:
            reasons.append(f"crosscheck:{name}")
    if gates.judge_tag_ok is not None and gates.judge_tag_ok < TAG_MIN:
        reasons.append("judge:tag")
    if gates.judge_context_sufficient is not None and gates.judge_context_sufficient < CONTEXT_MIN:
        reasons.append("judge:context")
    if gates.judge_dispute_risk is not None and gates.judge_dispute_risk > DISPUTE_MAX:
        reasons.append("judge:dispute-risk")
    return (Status.FLAGGED.value, reasons) if reasons else (Status.GREEN.value, [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_disposition.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/disposition.py tests/test_evidence_disposition.py
git commit -m "feat(evidence): green/flagged/dropped disposition rule

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Extractor (LLM, provider injected)

**Files:**
- Create: `src/evidence/extract.py`
- Test: `tests/test_evidence_extract.py`

**Interfaces:**
- Consumes: `QuoteCandidate` (Task 1); a provider with `.complete(prompt, *, max_tokens, temperature, system=None) -> str`.
- Produces: `build_extract_prompt(text, candidate_name) -> str`; `parse_extract(raw: str) -> list[QuoteCandidate]` (tolerates ```json fences); `extract_quotes(text, *, candidate_name, provider, max_tokens=1500) -> list[QuoteCandidate]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_extract.py
import json
from src.evidence.extract import parse_extract, extract_quotes

class FakeProvider:
    def __init__(self, responses): self._r = list(responses); self.prompts = []
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        self.prompts.append(prompt); return self._r.pop(0)

PAYLOAD = json.dumps({"quotes": [
    {"text": "We will build 30,000 units of housing.",
     "context": "At the forum, she said: We will build 30,000 units of housing.",
     "issue": "housing", "date": "2026-05-01", "setting": "candidate forum",
     "is_own_words": True, "is_primary_venue": True,
     "reported_event": None, "primary_handle": None}]})

def test_parse_extract_reads_fenced_json():
    out = parse_extract("```json\n" + PAYLOAD + "\n```")
    assert len(out) == 1 and out[0].issue == "housing"
    assert out[0].text.startswith("We will build")

def test_extract_quotes_calls_provider_and_parses():
    p = FakeProvider([PAYLOAD])
    out = extract_quotes("...source text...", candidate_name="Karen Bass", provider=p)
    assert out[0].is_primary_venue is True
    assert "Karen Bass" in p.prompts[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_extract.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/extract.py
from __future__ import annotations
import json
import re
from .models import QuoteCandidate

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)

_SYSTEM = ("You extract a politician's own VERBATIM sentences that state a view "
           "on an issue. Never paraphrase. Respond with ONLY the requested JSON.")

_INSTRUCTIONS = """From the SOURCE below, extract sentences spoken or written by {name}
that state a forward-looking view on a policy issue. Rules:
- VERBATIM only — copy the exact words from the SOURCE; never summarize or reword.
- One claim per quote. Trim only filler; mark substantive cuts with … .
- issue = a short lowercase topic label (e.g. "housing", "homelessness", "policing").
- is_own_words: true only if these are {name}'s own words (not the author's or an
  interviewer's).
- is_primary_venue: true if the SOURCE is {name}'s own venue (their site/official
  page/op-ed) or an outlet's OWN interview/Q&A with them; false if the SOURCE is
  reporting on a separate event where {name} spoke.
- If is_primary_venue is false and the SOURCE names a spoken event ({name} said X at
  a debate/town-hall/interview/podcast), set reported_event to "<event>, <date>" and,
  if the SOURCE links the primary (e.g. a YouTube URL), set primary_handle to it.
Return JSON: {{"quotes": [{{"text","context","issue","date","setting",
"is_own_words","is_primary_venue","reported_event","primary_handle"}}]}}.
context = the surrounding passage from the SOURCE (enough to vet the quote).

SOURCE:
{text}
"""


def build_extract_prompt(text: str, candidate_name: str) -> str:
    return _INSTRUCTIONS.format(name=candidate_name, text=text[:60000])


def parse_extract(raw: str) -> list:
    m = _FENCE.search(raw or "")
    payload = m.group(1) if m else (raw or "")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    out = []
    for q in data.get("quotes", []):
        if not (q.get("text") or "").strip():
            continue
        out.append(QuoteCandidate(
            text=q["text"].strip(), context=(q.get("context") or "").strip(),
            issue=(q.get("issue") or "").strip().lower(), date=q.get("date"),
            setting=q.get("setting"), is_own_words=bool(q.get("is_own_words", True)),
            is_primary_venue=bool(q.get("is_primary_venue", True)),
            reported_event=q.get("reported_event"),
            primary_handle=q.get("primary_handle")))
    return out


def extract_quotes(text, *, candidate_name, provider, max_tokens=1500) -> list:
    raw = provider.complete(build_extract_prompt(text, candidate_name),
                            max_tokens=max_tokens, temperature=0.0, system=_SYSTEM)
    return parse_extract(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_extract.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/extract.py tests/test_evidence_extract.py
git commit -m "feat(evidence): verbatim quote extractor (provider-injected)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Independent cross-checker (LLM)

**Files:**
- Create: `src/evidence/crosscheck.py`
- Test: `tests/test_evidence_crosscheck.py`

**Interfaces:**
- Consumes: `QuoteCandidate`, `CrossCheckVerdict` (Task 1); a provider.
- Produces: `build_crosscheck_prompt(cand, source_text, candidate_name) -> str`; `parse_crosscheck(raw) -> CrossCheckVerdict`; `crosscheck(cand, source_text, *, candidate_name, provider, extractor_issue, max_tokens=400) -> CrossCheckVerdict`. `tag_agree` is computed by comparing the cross-checker's independent `issue` against `extractor_issue` (case-insensitive), NOT taken from the model.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_crosscheck.py
import json
from src.evidence.crosscheck import parse_crosscheck, crosscheck

class FakeProvider:
    def __init__(self, r): self._r=list(r); self.prompts=[]
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        self.prompts.append(prompt); return self._r.pop(0)

def test_parse_crosscheck():
    v = parse_crosscheck(json.dumps({"own_words": True, "in_context": True,
        "primary": True, "issue": "housing", "notes": "ok"}))
    assert v.own_words and v.primary and v.issue == "housing"

def test_tag_agree_is_computed_from_issue_match():
    p = FakeProvider([json.dumps({"own_words": True, "in_context": True,
        "primary": True, "issue": "Housing", "notes": ""})])
    from src.evidence.models import QuoteCandidate
    cand = QuoteCandidate(text="We will build.", context="…", issue="housing")
    v = crosscheck(cand, "…We will build…", candidate_name="Bass", provider=p,
                   extractor_issue="housing")
    assert v.tag_agree is True

def test_tag_disagreement_when_issues_differ():
    p = FakeProvider([json.dumps({"own_words": True, "in_context": True,
        "primary": True, "issue": "transportation", "notes": ""})])
    from src.evidence.models import QuoteCandidate
    cand = QuoteCandidate(text="We will build.", context="…", issue="housing")
    v = crosscheck(cand, "…We will build…", candidate_name="Bass", provider=p,
                   extractor_issue="housing")
    assert v.tag_agree is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_crosscheck.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/crosscheck.py
from __future__ import annotations
import json
import re
from .models import CrossCheckVerdict

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)
_SYSTEM = ("You are an independent fact-checker. Read the SOURCE cold and judge a "
           "proposed quote. Respond with ONLY the requested JSON.")

_INSTRUCTIONS = """A proposed evidence quote attributed to {name}:
QUOTE: {quote}
CONTEXT SHOWN: {context}

Read the SOURCE below independently and judge:
- own_words: are these {name}'s own words in the SOURCE (not the author/interviewer)?
- in_context: does the SOURCE support this meaning (not cut to distort)?
- primary: is the SOURCE {name}'s own venue or an outlet's own interview with them
  (true), versus reporting on a separate event (false)?
- issue: the single lowercase topic label YOU think this quote answers.
Return JSON: {{"own_words","in_context","primary","issue","notes"}}.

SOURCE:
{text}
"""


def build_crosscheck_prompt(cand, source_text, candidate_name) -> str:
    return _INSTRUCTIONS.format(name=candidate_name, quote=cand.text,
                                context=cand.context, text=source_text[:60000])


def parse_crosscheck(raw: str) -> CrossCheckVerdict:
    m = _FENCE.search(raw or "")
    payload = m.group(1) if m else (raw or "")
    try:
        d = json.loads(payload)
    except json.JSONDecodeError:
        return CrossCheckVerdict(False, False, False, False, None, "unparseable")
    return CrossCheckVerdict(
        own_words=bool(d.get("own_words")), in_context=bool(d.get("in_context")),
        primary=bool(d.get("primary")), tag_agree=False,
        issue=(d.get("issue") or None), notes=(d.get("notes") or ""))


def crosscheck(cand, source_text, *, candidate_name, provider, extractor_issue,
               max_tokens=400) -> CrossCheckVerdict:
    raw = provider.complete(
        build_crosscheck_prompt(cand, source_text, candidate_name),
        max_tokens=max_tokens, temperature=0.0, system=_SYSTEM)
    v = parse_crosscheck(raw)
    v.tag_agree = bool(v.issue and extractor_issue
                       and v.issue.strip().lower() == extractor_issue.strip().lower())
    return v
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_crosscheck.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/crosscheck.py tests/test_evidence_crosscheck.py
git commit -m "feat(evidence): independent cross-checker with computed tag agreement

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Judge (LLM)

**Files:**
- Create: `src/evidence/judge.py`
- Test: `tests/test_evidence_judge.py`

**Interfaces:**
- Consumes: `QuoteCandidate`, `JudgeScores` (Task 1); a provider.
- Produces: `build_judge_prompt(cand) -> str`; `parse_judge(raw) -> JudgeScores` (clamps to [0,1], missing → tag_ok/context 0.0, dispute_risk 1.0 = worst); `judge(cand, *, provider, max_tokens=300) -> JudgeScores`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_judge.py
import json
from src.evidence.models import QuoteCandidate
from src.evidence.judge import parse_judge, judge

class FakeProvider:
    def __init__(self, r): self._r=list(r)
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        return self._r.pop(0)

def test_parse_clamps_and_defaults():
    s = parse_judge(json.dumps({"tag_ok": 1.4, "context_sufficient": 0.8}))
    assert s.tag_ok == 1.0 and s.context_sufficient == 0.8 and s.dispute_risk == 1.0

def test_judge_calls_provider():
    p = FakeProvider([json.dumps({"tag_ok": 0.9, "context_sufficient": 0.9,
                                  "dispute_risk": 0.1, "notes": "clean"})])
    cand = QuoteCandidate(text="We will build.", context="…", issue="housing")
    s = judge(cand, provider=p)
    assert s.dispute_risk == 0.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_judge.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/judge.py
from __future__ import annotations
import json
import re
from .models import JudgeScores

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)
_SYSTEM = ("You score a proposed evidence quote. Respond with ONLY the requested "
           "JSON. Scores are 0..1.")

_INSTRUCTIONS = """Score this quote as ranking/compass evidence.
QUOTE: {quote}
ISSUE TAG: {issue}
CONTEXT: {context}

- tag_ok: does the quote actually answer the ISSUE (1) or is it off-question (0)?
- context_sufficient: is the CONTEXT enough for a reader to vet the quote (1) or thin (0)?
- dispute_risk: how likely the speaker could say "I never said that" / "out of context"
  (0 = safe, 1 = high risk).
Return JSON: {{"tag_ok","context_sufficient","dispute_risk","notes"}}.
"""


def build_judge_prompt(cand) -> str:
    return _INSTRUCTIONS.format(quote=cand.text, issue=cand.issue, context=cand.context)


def _clamp(v, default):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def parse_judge(raw: str) -> JudgeScores:
    m = _FENCE.search(raw or "")
    payload = m.group(1) if m else (raw or "")
    try:
        d = json.loads(payload)
    except json.JSONDecodeError:
        d = {}
    return JudgeScores(
        tag_ok=_clamp(d.get("tag_ok"), 0.0),
        context_sufficient=_clamp(d.get("context_sufficient"), 0.0),
        dispute_risk=_clamp(d.get("dispute_risk"), 1.0),
        notes=(d.get("notes") or ""))


def judge(cand, *, provider, max_tokens=300) -> JudgeScores:
    raw = provider.complete(build_judge_prompt(cand), max_tokens=max_tokens,
                            temperature=0.0, system=_SYSTEM)
    return parse_judge(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_judge.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/judge.py tests/test_evidence_judge.py
git commit -m "feat(evidence): judge for tag/context/dispute-risk scoring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Leads shaping

**Files:**
- Create: `src/evidence/leads.py`
- Test: `tests/test_evidence_leads.py`

**Interfaces:**
- Consumes: `QuoteCandidate`, `Lead` (Task 1).
- Produces: `to_lead(cand: QuoteCandidate, *, politician_id, secondary_url) -> Lead`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_leads.py
from src.evidence.models import QuoteCandidate
from src.evidence.leads import to_lead

def test_to_lead_carries_reported_text_and_marker():
    cand = QuoteCandidate(text="I will end street homelessness.", context="…",
                          issue="homelessness", date="2026-05-01",
                          is_own_words=True, is_primary_venue=False,
                          reported_event="LAist debate, 2026-05-01",
                          primary_handle="https://youtu.be/xyz")
    lead = to_lead(cand, politician_id="p1", secondary_url="https://laist.com/story")
    assert lead.reported_text == "I will end street homelessness."
    assert lead.event == "LAist debate, 2026-05-01"
    assert lead.primary_handle == "https://youtu.be/xyz"
    assert "NOT verified against a primary" in lead.note
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_leads.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/leads.py
from __future__ import annotations
from .models import Lead, QuoteCandidate


def to_lead(cand: QuoteCandidate, *, politician_id: str, secondary_url: str) -> Lead:
    return Lead(
        politician_id=politician_id,
        reported_text=cand.text,
        issue=cand.issue,
        event=cand.reported_event or (cand.setting or "unspecified event"),
        secondary_url=secondary_url,
        primary_handle=cand.primary_handle)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_leads.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/leads.py tests/test_evidence_leads.py
git commit -m "feat(evidence): chase-the-primary lead shaping

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Eval scorer

**Files:**
- Create: `src/evidence/eval.py`
- Test: `tests/test_evidence_eval.py`

**Interfaces:**
- Consumes: `EvidenceItem`, `Lead`, `Status` (Task 1); `triage.registrable_domain` (Task 3).
- Produces: dataclass `Metrics`; `score(items: list[EvidenceItem], leads: list[Lead], gold: dict) -> Metrics`. `gold` shape: `{"labels": {"<item_key>": "green"|"reject"}, "recall_sample": {"<source_url>": <int expected>}}` where `item_key = f"{politician_id}|{source_url}|{verbatim_text[:60]}"`. Precision = agreed-green / labeled; recall = found-on-sampled-sources / expected.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_eval.py
from src.evidence.models import EvidenceItem, GateResults, SourceType, Status
from src.evidence.eval import score, item_key

def _item(pid, url, text, status, src=SourceType.PRIMARY):
    return EvidenceItem(politician_id=pid, issue="housing", evidence_type="quote",
        verbatim_text=text, source_url=url, cited_via=None, context="…",
        deep_link=url, source_type=src,
        gates=GateResults(verbatim=(status != Status.DROPPED.value)),
        status=status, status_reasons=[], provenance={})

def test_counts_and_rates():
    items = [_item("p1","https://a.com","We build","green"),
             _item("p1","https://a.com","We tax","flagged"),
             _item("p1","https://x.com","made up","dropped")]
    m = score(items, leads=[], gold={"labels": {}, "recall_sample": {}})
    assert m.green == 1 and m.flagged == 1 and m.dropped == 1
    assert 0.0 <= m.primary_source_rate <= 1.0

def test_precision_recall_against_gold():
    it = _item("p1","https://a.com","We build 30k units","green")
    gold = {"labels": {item_key(it): "green"},
            "recall_sample": {"https://a.com": 2}}
    m = score([it], leads=[], gold=gold)
    assert m.precision == 1.0
    assert m.recall == 0.5   # 1 found of 2 expected on the sampled source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_eval.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/eval.py
from __future__ import annotations
from dataclasses import dataclass, field
from .models import Status
from .triage import registrable_domain


def item_key(item) -> str:
    return f"{item.politician_id}|{item.source_url}|{item.verbatim_text[:60]}"


@dataclass
class Metrics:
    total: int = 0
    green: int = 0
    flagged: int = 0
    dropped: int = 0
    leads: int = 0
    verbatim_pass_rate: float = 0.0
    primary_source_rate: float = 0.0
    per_domain_yield: dict = field(default_factory=dict)
    precision: "float | None" = None
    recall: "float | None" = None


def score(items, leads, gold) -> Metrics:
    m = Metrics(total=len(items), leads=len(leads))
    verbatim_pass = 0
    green_items = []
    for it in items:
        m.green += it.status == Status.GREEN.value
        m.flagged += it.status == Status.FLAGGED.value
        m.dropped += it.status == Status.DROPPED.value
        verbatim_pass += bool(it.gates.verbatim)
        if it.status == Status.GREEN.value:
            green_items.append(it)
            d = registrable_domain(it.source_url)
            m.per_domain_yield[d] = m.per_domain_yield.get(d, 0) + 1
    m.verbatim_pass_rate = verbatim_pass / m.total if m.total else 0.0
    if green_items:
        prim = sum(1 for it in green_items if it.source_type == "primary")
        m.primary_source_rate = prim / len(green_items)

    labels = (gold or {}).get("labels", {})
    if labels:
        agree = sum(1 for it in green_items if labels.get(item_key(it)) == "green")
        labeled = sum(1 for it in items if item_key(it) in labels)
        m.precision = agree / labeled if labeled else None

    sample = (gold or {}).get("recall_sample", {})
    if sample:
        found = expected = 0
        for url, n in sample.items():
            expected += int(n)
            found += min(int(n), sum(1 for it in green_items if it.source_url == url))
        m.recall = found / expected if expected else None
    return m
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_eval.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/eval.py tests/test_evidence_eval.py
git commit -m "feat(evidence): offline eval scorer (rates + precision/recall vs gold)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Report + review page renderers

**Files:**
- Create: `src/evidence/report.py`
- Test: `tests/test_evidence_report.py`

**Interfaces:**
- Consumes: `Metrics` (Task 9); `EvidenceItem`, `Lead` (Task 1).
- Produces: `render_report(metrics: Metrics, scope_label: str) -> str` (Markdown); `render_review_html(items, leads, scope_label) -> str` (self-contained HTML, HTML-escaped content).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_report.py
from src.evidence.eval import Metrics
from src.evidence.models import EvidenceItem, GateResults, SourceType, Status, Lead
from src.evidence.report import render_report, render_review_html

def test_render_report_has_headline_metrics():
    m = Metrics(total=3, green=1, flagged=1, dropped=1, leads=2,
                verbatim_pass_rate=0.66, primary_source_rate=1.0,
                per_domain_yield={"laist.com": 1}, precision=1.0, recall=0.5)
    md = render_report(m, "LA Mayor")
    assert "LA Mayor" in md and "green" in md.lower()
    assert "0.5" in md and "precision" in md.lower()

def test_render_review_html_escapes_and_groups():
    it = EvidenceItem(politician_id="p1", issue="housing", evidence_type="quote",
        verbatim_text="We will <build> 30k", source_url="https://a.com",
        cited_via=None, context="…", deep_link="https://a.com",
        source_type=SourceType.PRIMARY, gates=GateResults(verbatim=True),
        status=Status.GREEN.value, status_reasons=[], provenance={})
    html = render_review_html([it], [Lead("p1","reported","crime","debate",
        "https://n.com", None)], "LA Mayor")
    assert "&lt;build&gt;" in html          # escaped
    assert "green" in html.lower() and "leads" in html.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_report.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/report.py
from __future__ import annotations
from html import escape
from .models import Status


def render_report(metrics, scope_label: str) -> str:
    m = metrics
    def pct(x): return "n/a" if x is None else f"{x:.2f}"
    lines = [f"# Evidence trust-core eval — {scope_label}", "",
             f"- items: {m.total}  (green {m.green} / flagged {m.flagged} / dropped {m.dropped})",
             f"- leads (chase-the-primary): {m.leads}",
             f"- verbatim pass rate: {pct(m.verbatim_pass_rate)}",
             f"- primary-source rate (of green): {pct(m.primary_source_rate)}",
             f"- precision (vs gold): {pct(m.precision)}",
             f"- recall (vs gold sample): {pct(m.recall)}", "",
             "## Per-domain green yield"]
    for d, n in sorted(m.per_domain_yield.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {d}: {n}")
    return "\n".join(lines) + "\n"


def _card(it) -> str:
    return (f'<div class="card {escape(it.status)}">'
            f'<div class="q">{escape(it.verbatim_text)}</div>'
            f'<div class="meta">{escape(it.issue)} · {escape(str(it.source_type))} · '
            f'{escape(", ".join(it.status_reasons))}</div>'
            f'<div class="ctx">{escape(it.context)}</div>'
            f'<a href="{escape(it.deep_link)}" target="_blank">source</a></div>')


def render_review_html(items, leads, scope_label: str) -> str:
    buckets = {Status.GREEN.value: [], Status.FLAGGED.value: [], Status.DROPPED.value: []}
    for it in items:
        buckets.setdefault(it.status, []).append(it)
    body = [f"<h1>Evidence review — {escape(scope_label)}</h1>"]
    for st in (Status.GREEN.value, Status.FLAGGED.value, Status.DROPPED.value):
        body.append(f"<h2>{st} ({len(buckets.get(st, []))})</h2>")
        body += [_card(it) for it in buckets.get(st, [])]
    body.append(f"<h2>Leads ({len(leads)})</h2>")
    for ld in leads:
        body.append(f'<div class="lead"><b>{escape(ld.issue)}</b> — '
                    f'{escape(ld.reported_text)}<br><i>{escape(ld.note)}</i><br>'
                    f'event: {escape(ld.event)} · '
                    f'<a href="{escape(ld.secondary_url)}" target="_blank">secondary</a></div>')
    style = ("<style>body{font:14px system-ui;margin:2rem;max-width:52rem}"
             ".card,.lead{border:1px solid #ddd;border-radius:8px;padding:.6rem;margin:.5rem 0}"
             ".green{border-left:5px solid #2e7d32}.flagged{border-left:5px solid #ed6c02}"
             ".dropped{border-left:5px solid #c62828;opacity:.7}.q{font-weight:600}"
             ".meta{color:#666;font-size:12px;margin:.3rem 0}.ctx{color:#333;font-size:13px}</style>")
    return f"<!doctype html><meta charset=utf-8>{style}" + "\n".join(body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/report.py tests/test_evidence_report.py
git commit -m "feat(evidence): eval report + static review-page renderers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 11: Pipeline orchestration

**Files:**
- Create: `src/evidence/pipeline.py`
- Test: `tests/test_evidence_pipeline.py`

**Interfaces:**
- Consumes: everything above. A `providers` object with `.extractor`, `.crosschecker`, `.judge` (each a `.complete(...)` provider); a `fetcher(url) -> str` callable; `verify.verbatim_ok`; `triage.classify_domain`; `disposition.decide`.
- Produces: dataclass `Providers`; `run_source(*, politician_id, source_url, cited_via, providers, fetcher, candidate_name, batch_id) -> tuple[list[EvidenceItem], list[Lead]]`; `run_candidate(*, politician_id, candidate_name, sources, providers, fetcher, batch_id) -> tuple[list[EvidenceItem], list[Lead]]` where `sources` is a list of `(source_url, cited_via)`.

Slice-1 rules encoded here:
- `classify_domain` → SCORECARD_QUIZ/DEAD → dropped item (no LLM). VOTE_RECORD → skipped (logged, not emitted). VIDEO_UNFETCHED → a lead with no reported text (event = the URL). POINTER → treated like a web page fetch attempt; extracted quotes from a pointer never become green (forced `source_type = POINTER`, which flags), but a reported-event quote still becomes a lead.
- Web page (domain None) or POINTER → fetch; empty fetch → DEAD dropped.
- For each extracted `QuoteCandidate`: if not `is_primary_venue` OR `reported_event` set → **lead** (via `to_lead`), no evidence item. Else verbatim gate; fail → dropped item. Pass → cross-check + judge → `decide` → item. `source_type = PRIMARY` when domain is None and `is_primary_venue`; `POINTER` when the domain was a pointer.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_pipeline.py
import json
from src.evidence.pipeline import Providers, run_source
from src.evidence.models import Status, SourceType

class FP:
    def __init__(self, r): self._r=list(r)
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        return self._r.pop(0)

SRC = "On her site Bass wrote: We will build 30,000 units of housing this term."

def _providers(extract, cross, judge):
    return Providers(extractor=FP([extract]), crosschecker=FP([cross]), judge=FP([judge]))

def test_primary_quote_that_passes_is_green():
    extract = json.dumps({"quotes": [{"text":"We will build 30,000 units of housing",
        "context": SRC, "issue":"housing","date":"2026","setting":"campaign site",
        "is_own_words":True,"is_primary_venue":True,"reported_event":None,
        "primary_handle":None}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,
                        "issue":"housing","notes":""})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1})
    items, leads = run_source(politician_id="p1",
        source_url="https://karenbass.com/housing", cited_via=None,
        providers=_providers(extract, cross, jud), fetcher=lambda u: SRC,
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.GREEN.value
    assert items[0].source_type == SourceType.PRIMARY.value

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/pipeline.py
from __future__ import annotations
from dataclasses import dataclass
from .models import (SourceType, Status, GateResults, EvidenceItem, Lead)
from .triage import classify_domain
from .verify import verbatim_ok
from .extract import extract_quotes
from .crosscheck import crosscheck
from .judge import judge as judge_quote
from .leads import to_lead
from .disposition import decide


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


def run_source(*, politician_id, source_url, cited_via, providers, fetcher,
               candidate_name, batch_id):
    items, leads = [], []
    domain_type = classify_domain(source_url)

    if domain_type in (SourceType.SCORECARD_QUIZ,):
        return [_dropped(politician_id, source_url, cited_via, "scorecard_quiz")], []
    if domain_type is SourceType.VOTE_RECORD:
        return [], []   # out of slice; logged by caller, not emitted
    if domain_type is SourceType.VIDEO_UNFETCHED:
        return [], [Lead(politician_id, "", "", f"video: {source_url}",
                         source_url, source_url)]

    text = fetcher(source_url) or ""
    if not text:
        return [_dropped(politician_id, source_url, cited_via, "dead")], []

    prov = {"extractor": getattr(providers.extractor, "model", "extractor"),
            "crosschecker": getattr(providers.crosschecker, "model", "crosschecker"),
            "judge": getattr(providers.judge, "model", "judge"), "batch": batch_id}

    for cand in extract_quotes(text, candidate_name=candidate_name,
                               provider=providers.extractor):
        if (not cand.is_primary_venue) or cand.reported_event:
            leads.append(to_lead(cand, politician_id=politician_id,
                                 secondary_url=source_url))
            continue
        if not verbatim_ok(cand.text, text):
            items.append(EvidenceItem(politician_id=politician_id, issue=cand.issue,
                evidence_type="quote", verbatim_text=cand.text, source_url=source_url,
                cited_via=cited_via, context=cand.context, deep_link=source_url,
                source_type=(domain_type.value if domain_type else SourceType.PRIMARY.value),
                gates=GateResults(verbatim=False), status=Status.DROPPED.value,
                status_reasons=["verbatim-fail"], provenance=prov))
            continue
        cc = crosscheck(cand, text, candidate_name=candidate_name,
                        provider=providers.crosschecker, extractor_issue=cand.issue)
        js = judge_quote(cand, provider=providers.judge)
        source_type = (SourceType.POINTER.value if domain_type is SourceType.POINTER
                       else SourceType.PRIMARY.value)
        gates = GateResults(verbatim=True, own_words=cc.own_words,
            in_context=cc.in_context, primary=cc.primary, tag_agree=cc.tag_agree,
            judge_tag_ok=js.tag_ok, judge_context_sufficient=js.context_sufficient,
            judge_dispute_risk=js.dispute_risk)
        status, reasons = decide(gates, source_type)
        items.append(EvidenceItem(politician_id=politician_id, issue=cand.issue,
            evidence_type="quote", verbatim_text=cand.text, source_url=source_url,
            cited_via=cited_via, context=cand.context, deep_link=source_url,
            source_type=source_type, gates=gates, status=status,
            status_reasons=reasons, provenance=prov))
    return items, leads


def run_candidate(*, politician_id, candidate_name, sources, providers, fetcher,
                  batch_id):
    all_items, all_leads = [], []
    for source_url, cited_via in sources:
        items, leads = run_source(politician_id=politician_id, source_url=source_url,
            cited_via=cited_via, providers=providers, fetcher=fetcher,
            candidate_name=candidate_name, batch_id=batch_id)
        all_items += items
        all_leads += leads
    return all_items, all_leads
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/pipeline.py tests/test_evidence_pipeline.py
git commit -m "feat(evidence): pipeline orchestration (triage→extract→verify→crosscheck→judge→decide)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 12: Read-only data access

**Files:**
- Create: `src/evidence/data.py`
- Test: `tests/test_evidence_data.py`

**Interfaces:**
- Produces: `database_url(env_file: str | None = None) -> str` (env `DATABASE_URL` > `--env-file` > `~/Documents/GitHub/ev-accounts/backend/.env`); `fetch_roster(conn, race_id) -> list[dict]` (`politician_id`, `name`); `fetch_cited_sources(conn, politician_id) -> list[tuple[str, str|None]]` (source_url, cited_via=None for now — the compass row is the citation origin); `load_topic_keys(conn) -> set[str]`. Only `database_url` is unit-tested (no live DB in tests); the fetchers are exercised by the runner.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_data.py
import os
from src.evidence.data import database_url

def test_database_url_prefers_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://from-env/x")
    assert database_url() == "postgres://from-env/x"

def test_database_url_reads_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    p = tmp_path / ".env"
    p.write_text('DATABASE_URL="postgres://from-file/y"\nOTHER=1\n')
    assert database_url(str(p)) == "postgres://from-file/y"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_data.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/data.py
from __future__ import annotations
import os
import pathlib
import re

_DEFAULT_ENV = pathlib.Path.home() / "Documents/GitHub/ev-accounts/backend/.env"


def _parse(text: str) -> "str | None":
    m = re.search(r'^DATABASE_URL\s*=\s*["\']?([^"\'\n]+)', text, re.M)
    return m.group(1) if m else None


def database_url(env_file: "str | None" = None) -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    path = pathlib.Path(env_file) if env_file else _DEFAULT_ENV
    if not path.is_file():
        raise FileNotFoundError(f"no env file at {path}")
    url = _parse(path.read_text())
    if not url:
        raise ValueError(f"no DATABASE_URL in {path}")
    return url


def connect(env_file=None):
    import psycopg2
    conn = psycopg2.connect(database_url(env_file))
    conn.set_session(readonly=True, autocommit=True)
    return conn


def fetch_roster(conn, race_id) -> list:
    import psycopg2.extras
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT rc.politician_id, "
        "COALESCE(p.full_name, TRIM(COALESCE(p.preferred_name,p.first_name)||' '||p.last_name)) name "
        "FROM essentials.race_candidates rc JOIN essentials.politicians p "
        "ON p.id = rc.politician_id WHERE rc.race_id = %s ORDER BY name",
        (race_id,))
    return [dict(r) for r in cur.fetchall()]


def fetch_cited_sources(conn, politician_id) -> list:
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT unnest(sources) FROM inform.politician_context "
        "WHERE politician_id = %s", (politician_id,))
    return [(r[0], None) for r in cur.fetchall() if r[0]]


def load_topic_keys(conn) -> set:
    cur = conn.cursor()
    cur.execute("SELECT lower(topic_key) FROM inform.compass_topics")
    return {r[0] for r in cur.fetchall()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_data.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/data.py tests/test_evidence_data.py
git commit -m "feat(evidence): read-only DB access for roster + cited sources

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 13: Manual online runner + gold template + README

**Files:**
- Create: `scripts/evidence_slice.py`
- Create: `docs/superpowers/spikes/2026-09-19-evidence-trust-core/README.md`
- Create: `docs/superpowers/spikes/2026-09-19-evidence-trust-core/gold_template.json`
- Test: `tests/test_evidence_runner.py` (imports + arg parsing only; no network)

**Interfaces:**
- Consumes: `data`, `pipeline`, `eval`, `report`, `src.discovery.feeds.fetch_page_text`, `src.llm_providers.get_provider`.
- Produces: a `main(argv)` with flags `--race` (default the LA Mayor id), `--candidate` (optional politician_id filter), `--limit` (sources per candidate), `--extractor/--crosschecker/--judge` (model keys, defaults `sonnet`/`gemini-flash`/`gpt5-mini`), `--env-file`, `--out` (default the spike dir), `--gold` (optional gold JSON path). Writes `evidence_items.json`, `leads.json`, `eval_report.md`, `review.html`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_runner.py
import importlib
def test_runner_imports_and_parses_args():
    mod = importlib.import_module("scripts.evidence_slice")
    args = mod.build_parser().parse_args(["--race", "R", "--limit", "5"])
    assert args.race == "R" and args.limit == 5
    assert args.extractor == "sonnet" and args.crosschecker == "gemini-flash"
```

Note: add `tests/__init__.py` and a `conftest.py` `sys.path` insert only if the repo's existing tests don't already import `scripts.*`; check an existing test first and mirror its pattern.

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: scripts.evidence_slice`.

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# scripts/evidence_slice.py
"""Manual ONLINE runner for the evidence trust-core slice (LA Mayor).

Reads the candidates' already-cited compass-research sources, runs the
extract -> verbatim gate -> independent cross-check -> judge pipeline, and
writes artifacts (no DB writes). Keys from the main-checkout .env.local or
--env-file.

  ~/Documents/GitHub/on-the-record/.venv/bin/python scripts/evidence_slice.py \
      [--race ID] [--limit N] [--gold path.json]
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.evidence import data, pipeline, report
from src.evidence.eval import score
from src.evidence.pipeline import Providers
from src.llm_providers import get_provider
from src.discovery.feeds import fetch_page_text

LA_MAYOR = "9e888818-c50b-4c61-a106-a0839ff2479d"
SPIKE_DIR = (pathlib.Path(__file__).resolve().parents[1]
             / "docs/superpowers/spikes/2026-09-19-evidence-trust-core")


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--race", default=LA_MAYOR)
    ap.add_argument("--candidate", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--extractor", default="sonnet")
    ap.add_argument("--crosschecker", default="gemini-flash")
    ap.add_argument("--judge", default="gpt5-mini")
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--out", default=str(SPIKE_DIR))
    ap.add_argument("--gold", default=None)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    providers = Providers(extractor=get_provider(args.extractor),
                          crosschecker=get_provider(args.crosschecker),
                          judge=get_provider(args.judge))
    conn = data.connect(args.env_file)
    roster = data.fetch_roster(conn, args.race)
    if args.candidate:
        roster = [r for r in roster if r["politician_id"] == args.candidate]

    all_items, all_leads = [], []
    for cand in roster:
        sources = data.fetch_cited_sources(conn, cand["politician_id"])
        if args.limit:
            sources = sources[:args.limit]
        items, leads = pipeline.run_candidate(
            politician_id=cand["politician_id"], candidate_name=cand["name"],
            sources=sources, providers=providers, fetcher=fetch_page_text,
            batch_id="evidence-slice-la-mayor")
        print(f"{cand['name']}: {len(items)} items, {len(leads)} leads "
              f"from {len(sources)} sources")
        all_items += items
        all_leads += leads

    gold = json.loads(pathlib.Path(args.gold).read_text()) if args.gold else {}
    metrics = score(all_items, all_leads, gold)

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evidence_items.json").write_text(
        json.dumps([it.to_json() for it in all_items], indent=2))
    (out / "leads.json").write_text(
        json.dumps([ld.to_json() for ld in all_leads], indent=2))
    (out / "eval_report.md").write_text(report.render_report(metrics, "LA Mayor"))
    (out / "review.html").write_text(
        report.render_review_html(all_items, all_leads, "LA Mayor"))
    print(f"\nWrote artifacts to {out}")
    print(report.render_report(metrics, "LA Mayor"))


if __name__ == "__main__":
    main()
```

Also create `gold_template.json`:

```json
{
  "labels": {
    "<politician_id>|<source_url>|<first 60 chars of verbatim_text>": "green"
  },
  "recall_sample": {
    "<source_url a curator read fully>": 2
  }
}
```

And `README.md` documenting: purpose, how to run (with the main venv + `.env.local`), how to fill the gold from `review.html` (label ~20–30 items green/reject; list expected quote counts for a few sources), and the current baseline once first run. Note fetch caching is via `feeds` behavior; re-runs are network-bound.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_runner.py -v`
Expected: PASS. Then run the full offline suite:
Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_*.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/evidence_slice.py tests/test_evidence_runner.py \
  docs/superpowers/spikes/2026-09-19-evidence-trust-core/
git commit -m "feat(evidence): manual online runner + gold template + README

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 14: First live run + eval baseline (manual, online — human-gated)

**Files:**
- Modify: `docs/superpowers/spikes/2026-09-19-evidence-trust-core/README.md` (record baseline)
- Output (git-ignored or committed as a run snapshot, Chris's call): `evidence_items.json`, `leads.json`, `eval_report.md`, `review.html`

**This task runs the network + LLMs and needs Chris.** No new code unless a bug surfaces.

- [ ] **Step 1: Dry structural run on one candidate, small limit**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python scripts/evidence_slice.py --candidate 21c9e711-fb18-4afb-884f-08acd2b598ba --limit 5 --env-file ~/Documents/GitHub/on-the-record/.env.local`
Expected: writes artifacts; prints per-candidate counts and the report. Confirm `evidence_items.json` has real verbatim quotes whose `deep_link` opens the source.

- [ ] **Step 2: Full slice run (both candidates)**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python scripts/evidence_slice.py --env-file ~/Documents/GitHub/on-the-record/.env.local`
Expected: `review.html` groups green/flagged/dropped + leads.

- [ ] **Step 3: Chris labels the thin gold**

Open `review.html`; in `gold_template.json` → save as `gold.json`: label ~20–30 items `green`/`reject`; for ~3 sources Chris reads fully, set the expected green-quote count in `recall_sample`.

- [ ] **Step 4: Re-run with gold to get precision/recall**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python scripts/evidence_slice.py --gold docs/superpowers/spikes/2026-09-19-evidence-trust-core/gold.json --env-file ~/Documents/GitHub/on-the-record/.env.local`
Expected: `eval_report.md` shows verbatim pass rate (expect ~1.0 by construction), primary-source rate, precision, recall, lead yield.

- [ ] **Step 5: Record the baseline + commit the write-up**

Update the README with the baseline numbers and any calibration notes (threshold changes in `disposition.py` if the gold says the judge is mis-calibrated — a code change re-runs Tasks 4's tests). Commit:

```bash
git add docs/superpowers/spikes/2026-09-19-evidence-trust-core/
git commit -m "docs(evidence): first LA Mayor trust-core baseline + gold

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage.**
- Evidence item atom → Task 1. ✔
- Verbatim gate (deterministic, ellipsis-tolerant) → Task 2. ✔
- Triage (primary/pointer/scorecard/quiz/vote/video/dead) → Task 3 (domain rules) + Task 11 (primary-vs-secondary via extractor/cross-checker; the LLM judgement the spec assigns to steps 1/5). ✔
- Disposition green/flagged/dropped, green requires primary → Task 4 + Task 11. ✔
- Extractor / independent cross-check (different model) / judge → Tasks 5/6/7, wired in 11, distinct models in 13. ✔
- Ingestion leads carrying reported text + unverified marker → Tasks 1/8, wired in 11. ✔
- Eval harness: deterministic + LLM-judge + thin human gold; precision/recall → Tasks 9 + 14. ✔
- Outputs (evidence_items.json, leads.json, eval_report.md, review.html) → Tasks 10/13. ✔
- No DB writes / read-only input from `inform.politician_context` + roster → Task 12. ✔
- Model independence, main-venv execution, OpenRouter → Global Constraints + Tasks 11/13. ✔
- Non-goals (votes/actions, essentials writes, product page, derivation, video transcription, breadth) → not built; VOTE_RECORD skipped, VIDEO → lead. ✔

**2. Placeholder scan.** No "TBD"/"handle appropriately"; every code step has real code. The only human-judgement steps (Task 14) are explicitly manual/online and gated, not code placeholders. One acceptable deferral is documented: the README content is described rather than fully written, because it records numbers that only exist after Task 14's run.

**3. Type consistency.** `Providers(extractor, crosschecker, judge)`, `run_source(...)`/`run_candidate(...)`, `GateResults` field names, `SourceType`/`Status` string values, `to_json()`, `item_key`, `score(items, leads, gold)`, `render_report`/`render_review_html` — names and signatures match across Tasks 1, 9, 10, 11, 13. `crosscheck(...)` takes `extractor_issue` and computes `tag_agree` (Task 6) exactly as Task 11 calls it.

**Slice-1 simplification (flagged for the executor):** pointer-following is bounded — a POINTER page is fetched and any quotes from it are forced to `source_type=POINTER` (so they flag, never green), and reported-event quotes become leads. Recursive footnote-following to the cited primary is a documented future refinement, consistent with the spec's intent that green never comes from a pointer.
