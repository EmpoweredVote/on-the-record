# audit-quotes Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable `audit-quotes` skill (on-the-record) that sweeps `essentials.quotes` across all races, checks each quote/topic/race against the codified curation principles, produces a consolidated report, and applies gated fixes — and wire `publish-quotes` to auto-run it on newly inserted quotes.

**Architecture:** A Python CLI (`audit.py`) reuses the `ev-accounts/backend/.env` `DATABASE_URL` (same pattern as `publish-quotes/scripts/insert_quotes.py`). It resolves scope → runs a deterministic **mechanical** SQL pre-pass (pure check functions over fetched rows) → writes per-race **context bundles** for an LLM **judgment** pass that the skill fans out as parallel `Agent` subagents → aggregates all findings → renders a consolidated markdown report → drives `apply_fixes.py` for gated, per-race dry-run/commit fixes. Skill prose (`SKILL.md`, `CHECKS.md`) is the human/agent-facing workflow and check catalog. Principles source of truth: `essentials/docs/QUOTE-CURATION-PRINCIPLES.md`.

**Tech Stack:** Python 3 (on-the-record `.venv`, `psycopg2`), pytest, Markdown skill files. DB: ev-accounts Supabase (`essentials.quotes`, `inform.compass_topics`/`compass_stances`/`politician_answers`, `essentials.politicians`).

---

## File Structure

Created under `on-the-record/.claude/skills/audit-quotes/`:

- `SKILL.md` — workflow: scope → confirm → mechanical → fan-out judgment → portfolio → report → gated fixes.
- `CHECKS.md` — the check catalog (each check: id, level, principle, how detected, severity, fix-class) + the judgment-agent prompt template + the findings JSON schema.
- `scripts/db.py` — connection + scope resolution + row/context fetch (thin DB layer).
- `scripts/checks.py` — **pure** mechanical check functions (row dict → findings). No DB.
- `scripts/report.py` — **pure** findings-list → consolidated markdown string.
- `scripts/audit.py` — CLI: wires scope + mechanical checks + context-bundle output + scope summary + report write.
- `scripts/apply_fixes.py` — gated fix applier: fixes JSON → dry-run (transaction+rollback, prints diff) / `--commit`.
- `scripts/models.py` — shared dataclasses/constants (Finding, severities, fix-classes).
- `tests/test_checks.py`, `tests/test_report.py`, `tests/test_apply_fixes.py` — pytest.

Modified:
- `on-the-record/.claude/skills/publish-quotes/SKILL.md` — add final auto-run handoff step.

Report output: `on-the-record/docs/audits/<YYYY-MM-DD>-quote-audit[-<scope>].md`.

---

## Task 1: Skill scaffold + shared models

**Files:**
- Create: `.claude/skills/audit-quotes/scripts/models.py`
- Create: `.claude/skills/audit-quotes/scripts/__init__.py` (empty)
- Create: `.claude/skills/audit-quotes/tests/__init__.py` (empty)

- [ ] **Step 1: Create the models module**

```python
# .claude/skills/audit-quotes/scripts/models.py
"""Shared types/constants for the audit-quotes skill."""
from dataclasses import dataclass, asdict
from typing import Optional

SEVERITIES = ("high", "medium", "low")
FIX_CLASSES = ("mechanical", "guided", "decision-required")
LEVELS = ("quote", "topic", "portfolio")

@dataclass
class Finding:
    check_id: str          # e.g. "note-missing"
    level: str             # quote | topic | portfolio
    principle: str         # short human phrase, e.g. "editor_note required"
    severity: str          # high | medium | low
    fix_class: str         # mechanical | guided | decision-required
    what: str              # what's wrong, human-readable
    suggested_fix: str     # human-readable proposed fix
    quote_id: Optional[str] = None
    topic_key: Optional[str] = None
    race_id: Optional[str] = None
    candidate: Optional[str] = None
    # For mechanical fixes only: exact op the applier understands.
    fix_op: Optional[dict] = None   # {"kind": "set_field", "field": "editor_note", "value": "..."} etc.

    def __post_init__(self):
        assert self.severity in SEVERITIES, self.severity
        assert self.fix_class in FIX_CLASSES, self.fix_class
        assert self.level in LEVELS, self.level

    def to_dict(self):
        return asdict(self)
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/audit-quotes/scripts/models.py .claude/skills/audit-quotes/scripts/__init__.py .claude/skills/audit-quotes/tests/__init__.py
git commit -m "feat(audit-quotes): scaffold + Finding model"
```

---

## Task 2: Pure mechanical checks (TDD)

The deterministic checks operate on a **quote row dict** with keys: `id, candidate, topic_key, race_id, readrank_selected (bool), quote_text, deidentified_text, editor_note, source_name, source_url`. Topic-level checks operate on a **topic group**: `{race_id, topic_key, quotes: [row, ...]}`.

**Files:**
- Create: `.claude/skills/audit-quotes/scripts/checks.py`
- Test: `.claude/skills/audit-quotes/tests/test_checks.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_checks.py
from scripts.checks import (
    check_note_quality, check_deid_present, check_trailing_ellipsis,
    check_partisan_tell_in_blind, check_source_tier, topic_live_count, topic_min_candidates,
)

def row(**kw):
    base = dict(id="q1", candidate="A", topic_key="housing", race_id="r1",
                readrank_selected=True, quote_text="We must build more homes.",
                deidentified_text="We must build more homes.", editor_note="Verbatim, no edits.",
                source_name="www.youtube.com", source_url="https://youtu.be/x?t=1s")
    base.update(kw); return base

def test_note_missing_is_high_guided():
    f = check_note_quality(row(editor_note=None))
    assert f and f.check_id == "note-missing" and f.severity == "high" and f.fix_class == "guided"

def test_note_with_section_ref_flagged():
    f = check_note_quality(row(editor_note="Matches stance (§4.3); tier-1 debate."))
    assert f and f.check_id == "note-section-ref"

def test_note_too_long_flagged():
    long = "One sentence here. Two here. Three here. Four here."
    f = check_note_quality(row(editor_note=long))
    assert f and f.check_id == "note-too-long"

def test_good_note_passes():
    assert check_note_quality(row(editor_note="Clear housing supply position. Verbatim, no edits.")) is None

def test_deid_null_flagged():
    f = check_deid_present(row(deidentified_text=None))
    assert f and f.check_id == "deid-missing" and f.fix_class == "guided"

def test_trailing_ellipsis_flagged():
    f = check_trailing_ellipsis(row(quote_text="We must act …"))
    assert f and f.check_id == "trailing-ellipsis"

def test_partisan_tell_in_blind_flagged():
    f = check_partisan_tell_in_blind(row(deidentified_text="These Democrat policies failed."))
    assert f and f.check_id == "partisan-tell" and f.fix_class == "guided"

def test_source_tier_campaign_site_flagged():
    f = check_source_tier(row(source_url="https://www.xavierbecerra2026.com/housing", source_name="www.xavierbecerra2026.com"))
    assert f and f.check_id == "source-tier-4"

def test_topic_two_live_flagged():
    g = {"race_id": "r1", "topic_key": "housing",
         "quotes": [row(id="a", readrank_selected=True), row(id="b", readrank_selected=True)]}
    f = topic_live_count(g)
    assert f and f.check_id == "multiple-live" and f.severity == "high"

def test_topic_one_candidate_not_rankable():
    g = {"race_id": "r1", "topic_key": "housing", "quotes": [row(readrank_selected=True)]}
    f = topic_min_candidates(g)
    assert f and f.check_id == "not-rankable"
```

- [ ] **Step 2: Run to verify fail**

Run: `cd .claude/skills/audit-quotes && ../../../.venv/bin/python -m pytest tests/test_checks.py -q`
Expected: FAIL (ImportError / module not found).

- [ ] **Step 3: Implement checks.py**

```python
# scripts/checks.py
"""Pure, deterministic mechanical checks. Input = plain dicts. No DB, no I/O."""
import re
from typing import Optional
from scripts.models import Finding

_PARTISAN = re.compile(r"\b(Democrat|Democratic|Republican|GOP|MAGA|my party)\b", re.I)
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
CAMPAIGN_SITE = re.compile(r"(campaign|for\w*|20\d\d)\.com|/(issues|platform|homelessness|housing|immigration)\b", re.I)

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
    live = [q for q in group["quotes"] if q.get("readrank_selected")]
    if len(live) <= 1:
        return None
    return Finding(check_id="multiple-live", level="topic", topic_key=group["topic_key"], race_id=group["race_id"],
                   principle="one live quote per candidate per topic", severity="high", fix_class="decision-required",
                   what=f"{len(live)} live quotes in this topic for the same candidate(s): {[q['id'] for q in live]}",
                   suggested_fix="Demote all but one to draft.")

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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd .claude/skills/audit-quotes && ../../../.venv/bin/python -m pytest tests/test_checks.py -q`
Expected: PASS (all tests green).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/audit-quotes/scripts/checks.py .claude/skills/audit-quotes/tests/test_checks.py
git commit -m "feat(audit-quotes): pure mechanical checks with tests"
```

---

## Task 3: DB layer (scope resolution + context fetch)

**Files:**
- Create: `.claude/skills/audit-quotes/scripts/db.py`

- [ ] **Step 1: Implement db.py**

Reads `DATABASE_URL` from `ev-accounts/backend/.env` (like `publish-quotes/REFERENCE.md`). Resolves paths relative to this repo so the skill works from the on-the-record root.

```python
# scripts/db.py
"""Thin DB layer for the audit. Read-only except apply_fixes.py."""
import os, re, pathlib
import psycopg2, psycopg2.extras

def _database_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    # ev-accounts/backend/.env sits beside the on-the-record repo
    here = pathlib.Path(__file__).resolve()
    repo = here.parents[4]  # .../on-the-record
    env = repo.parent / "ev-accounts" / "backend" / ".env"
    for line in env.read_text().splitlines():
        m = re.match(r'\s*DATABASE_URL\s*=\s*"?([^"\n]+)"?', line)
        if m:
            return m.group(1)
    raise RuntimeError("DATABASE_URL not found in env or ev-accounts/backend/.env")

def connect():
    return psycopg2.connect(_database_url(), sslmode="require")

SCOPE_SQL = """
SELECT q.id, q.topic_key, q.readrank_selected, q.quote_text, q.deidentified_text,
       q.editor_note, q.source_name, q.source_url,
       p.full_name AS candidate, rc.race_id::text AS race_id
FROM essentials.quotes q
JOIN essentials.politicians p ON p.id = q.politician_id
LEFT JOIN essentials.race_candidates rc ON rc.politician_id = q.politician_id
WHERE (%(ids)s IS NULL OR q.id = ANY(%(ids)s))
  AND (%(candidate)s IS NULL OR lower(p.full_name) = lower(%(candidate)s))
  AND (%(topic)s IS NULL OR q.topic_key = %(topic)s)
  AND (%(drafts)s OR q.readrank_selected = true)
ORDER BY race_id, q.topic_key, q.readrank_selected DESC
"""

def fetch_rows(conn, ids=None, candidate=None, topic=None, include_drafts=False):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(SCOPE_SQL, dict(ids=ids, candidate=candidate, topic=topic, drafts=include_drafts))
        return [dict(r) for r in cur.fetchall()]

def fetch_stance(conn, candidate, topic_key):
    """Returns {'value': float|None, 'question': str, 'chairs': [{'v','text'}...]} or None."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
          SELECT t.question_text,
                 (SELECT a.value FROM inform.politician_answers a
                  JOIN essentials.politicians p ON p.id=a.politician_id
                  WHERE a.topic_id=t.id AND lower(p.full_name)=lower(%s)) AS value,
                 (SELECT json_agg(json_build_object('v', s.value, 'text', s.text) ORDER BY s.value)
                  FROM inform.compass_stances s WHERE s.topic_id=t.id) AS chairs
          FROM inform.compass_topics t WHERE t.topic_key=%s
        """, (candidate, topic_key))
        row = cur.fetchone()
        return dict(row) if row else None
```

> **Verify the `race_candidates` join at implementation time.** If quotes don't reliably map to a race via `race_candidates`, fall back to grouping by candidate only and set `race_id = candidate`'s primary race, or omit race grouping and note it. Confirm with: `\d essentials.race_candidates`.

- [ ] **Step 2: Smoke-test the connection**

Run: `cd .claude/skills/audit-quotes && ../../../.venv/bin/python -c "from scripts.db import connect, fetch_rows; c=connect(); print(len(fetch_rows(c, candidate='Steve Hilton')))"`
Expected: prints an integer > 0 (e.g. `37`).

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/audit-quotes/scripts/db.py
git commit -m "feat(audit-quotes): read-only DB layer (scope + stance fetch)"
```

---

## Task 4: Report renderer (TDD)

**Files:**
- Create: `.claude/skills/audit-quotes/scripts/report.py`
- Test: `.claude/skills/audit-quotes/tests/test_report.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_report.py
from scripts.models import Finding
from scripts.report import render

def f(**kw):
    d = dict(check_id="note-missing", level="quote", principle="editor_note required",
             severity="high", fix_class="mechanical", what="empty", suggested_fix="write it",
             quote_id="q1", topic_key="housing", race_id="r1", candidate="A")
    d.update(kw); return Finding(**d)

def test_render_groups_by_race_and_counts():
    md = render([f(), f(severity="low", fix_class="guided", check_id="note-too-long")], scope_label="all races")
    assert "# Quote Audit — all races" in md
    assert "2 findings" in md
    assert "race r1" in md
    assert "high" in md and "mechanical" in md

def test_render_empty():
    md = render([], scope_label="CA Governor")
    assert "No findings" in md
```

- [ ] **Step 2: Run to verify fail**

Run: `cd .claude/skills/audit-quotes && ../../../.venv/bin/python -m pytest tests/test_report.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement report.py**

```python
# scripts/report.py
"""Pure: findings list -> consolidated markdown. No I/O."""
from collections import Counter, defaultdict
from scripts.models import SEVERITIES

def render(findings, scope_label: str) -> str:
    out = [f"# Quote Audit — {scope_label}", ""]
    if not findings:
        out.append("No findings. ✅")
        return "\n".join(out)
    sev = Counter(f.severity for f in findings)
    out.append(f"**{len(findings)} findings** — "
               + ", ".join(f"{sev.get(s,0)} {s}" for s in SEVERITIES))
    out.append("")
    by_race = defaultdict(list)
    for f in findings:
        by_race[f.race_id or "(no race)"].append(f)
    # cross-race summary
    out.append("## Summary by race")
    for race, fs in sorted(by_race.items(), key=lambda kv: -len(kv[1])):
        c = Counter(x.severity for x in fs)
        out.append(f"- **race {race}** — {len(fs)} findings ({c.get('high',0)} high, {c.get('medium',0)} med, {c.get('low',0)} low)")
    out.append("")
    # per-race detail, severity then fix_class
    order = {s: i for i, s in enumerate(SEVERITIES)}
    for race, fs in sorted(by_race.items()):
        out.append(f"## race {race}")
        for f in sorted(fs, key=lambda x: (order[x.severity], x.fix_class, x.topic_key or "")):
            tgt = f.quote_id or f.topic_key or race
            out.append(f"- `{f.severity}` · `{f.fix_class}` · **{f.check_id}** ({f.level}) "
                       f"— {f.candidate or ''} / {f.topic_key or ''} [{tgt}]")
            out.append(f"    - {f.what}")
            out.append(f"    - fix: {f.suggested_fix}")
        out.append("")
    return "\n".join(out)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd .claude/skills/audit-quotes && ../../../.venv/bin/python -m pytest tests/test_report.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/audit-quotes/scripts/report.py .claude/skills/audit-quotes/tests/test_report.py
git commit -m "feat(audit-quotes): consolidated report renderer with tests"
```

---

## Task 5: audit.py CLI (scope, mechanical pass, context bundles, report)

**Files:**
- Create: `.claude/skills/audit-quotes/scripts/audit.py`

- [ ] **Step 1: Implement audit.py**

```python
# scripts/audit.py
"""audit-quotes CLI. Default: sweep all live quotes across all races.
Modes:
  (default)         resolve scope, run mechanical checks, write context bundles + mechanical report
Flags: --candidate NAME  --topic KEY  --ids id1,id2  --include-drafts  --out DIR  --scope-label LABEL
"""
import argparse, json, pathlib, sys, datetime
from scripts.db import connect, fetch_rows, fetch_stance
from scripts.checks import run_mechanical
from scripts.report import render

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate"); ap.add_argument("--topic"); ap.add_argument("--ids")
    ap.add_argument("--include-drafts", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--scope-label", default="all races")
    a = ap.parse_args()
    ids = a.ids.split(",") if a.ids else None

    conn = connect()
    rows = fetch_rows(conn, ids=ids, candidate=a.candidate, topic=a.topic, include_drafts=a.include_drafts)
    if not rows:
        print("No quotes matched scope."); return

    # Scope summary (the confirmation gate reads this)
    races = {r["race_id"] for r in rows}
    topics = {(r["race_id"], r["topic_key"]) for r in rows}
    print(f"SCOPE: {len(rows)} quotes | {len(races)} races | {len(topics)} race-topic groups | "
          f"drafts={'yes' if a.include_drafts else 'no'}")

    # Mechanical pass
    findings = run_mechanical(rows)
    print(f"MECHANICAL FINDINGS: {len(findings)}")

    # Context bundles for the judgment pass: one JSON per race, quotes grouped by topic, with stance+spectrum.
    run_dir = pathlib.Path(a.out or f".claude/skills/audit-quotes/.runs/{datetime.date.today()}")
    (run_dir / "context").mkdir(parents=True, exist_ok=True)
    by_race = {}
    for r in rows:
        by_race.setdefault(r["race_id"], []).append(r)
    stance_cache = {}
    for race, rrows in by_race.items():
        bundle = {"race_id": race, "topics": {}}
        for r in rrows:
            key = (r["candidate"], r["topic_key"])
            if key not in stance_cache:
                stance_cache[key] = fetch_stance(conn, r["candidate"], r["topic_key"])
            t = bundle["topics"].setdefault(r["topic_key"], {"topic_key": r["topic_key"], "quotes": []})
            t["quotes"].append({**r, "stance": stance_cache[key]})
        (run_dir / "context" / f"{race}.json").write_text(json.dumps(bundle, indent=2, default=str))

    (run_dir / "mechanical_findings.json").write_text(
        json.dumps([f.to_dict() for f in findings], indent=2, default=str))
    report_md = render(findings, scope_label=a.scope_label + " (mechanical only)")
    (run_dir / "mechanical_report.md").write_text(report_md)
    print(f"WROTE: {run_dir}/context/*.json, mechanical_findings.json, mechanical_report.md")
    print("NEXT: run the judgment pass (see SKILL.md), then merge findings and render the full report.")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run mechanical pass against a real candidate**

Run: `cd .claude/skills/audit-quotes && ../../../.venv/bin/python -m scripts.audit --candidate "Steve Hilton" --scope-label "Hilton"`
Expected: prints SCOPE / MECHANICAL FINDINGS counts and writes `.runs/<date>/context/*.json` + `mechanical_report.md`. Eyeball the report.

- [ ] **Step 3: Commit** (add `.runs/` to `.gitignore` first)

```bash
echo ".claude/skills/audit-quotes/.runs/" >> .gitignore
git add .claude/skills/audit-quotes/scripts/audit.py .gitignore
git commit -m "feat(audit-quotes): CLI — scope, mechanical pass, context bundles, report"
```

---

## Task 6: apply_fixes.py — gated dry-run/commit (TDD the SQL builder)

**Files:**
- Create: `.claude/skills/audit-quotes/scripts/apply_fixes.py`
- Test: `.claude/skills/audit-quotes/tests/test_apply_fixes.py`

A **fixes file** is a JSON list of ops: `{"kind": "set_field", "id": "...", "field": "editor_note", "value": "..."}` or `{"kind": "regex_sub", "id": "...", "field": "quote_text", "pattern": "...", "repl": ""}` or `{"kind": "set_live", "id": "...", "value": false}`. `build_statement(op)` returns `(sql, params)` — pure, tested. Applying runs them in one transaction, prints before/after, and rolls back unless `--commit`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_apply_fixes.py
from scripts.apply_fixes import build_statement

def test_set_field():
    sql, params = build_statement({"kind": "set_field", "id": "q1", "field": "editor_note", "value": "hi"})
    assert "UPDATE essentials.quotes SET editor_note = %s WHERE id = %s" == sql
    assert params == ["hi", "q1"]

def test_regex_sub_uses_regexp_replace():
    sql, params = build_statement({"kind": "regex_sub", "id": "q1", "field": "quote_text",
                                   "pattern": r"\s*…\s*$", "repl": ""})
    assert "regexp_replace(quote_text" in sql and params[-1] == "q1"

def test_set_live():
    sql, params = build_statement({"kind": "set_live", "id": "q1", "value": False})
    assert "readrank_selected = %s" in sql and params == [False, "q1"]

def test_rejects_unknown_field():
    import pytest
    with pytest.raises(ValueError):
        build_statement({"kind": "set_field", "id": "q1", "field": "politician_id", "value": "x"})
```

- [ ] **Step 2: Run to verify fail**

Run: `cd .claude/skills/audit-quotes && ../../../.venv/bin/python -m pytest tests/test_apply_fixes.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement apply_fixes.py**

```python
# scripts/apply_fixes.py
"""Gated fix applier. Dry-run (transaction+rollback) by default; --commit persists."""
import argparse, json, sys
from scripts.db import connect

ALLOWED_FIELDS = {"editor_note", "deidentified_text", "quote_text", "topic_key"}

def build_statement(op):
    kind = op["kind"]; qid = op["id"]
    if kind == "set_field":
        if op["field"] not in ALLOWED_FIELDS:
            raise ValueError(f"field not allowed: {op['field']}")
        return (f"UPDATE essentials.quotes SET {op['field']} = %s WHERE id = %s", [op["value"], qid])
    if kind == "regex_sub":
        if op["field"] not in ALLOWED_FIELDS:
            raise ValueError(f"field not allowed: {op['field']}")
        return (f"UPDATE essentials.quotes SET {op['field']} = regexp_replace({op['field']}, %s, %s) WHERE id = %s",
                [op["pattern"], op["repl"], qid])
    if kind == "set_live":
        return ("UPDATE essentials.quotes SET readrank_selected = %s WHERE id = %s", [bool(op["value"]), qid])
    raise ValueError(f"unknown op kind: {kind}")

def _snapshot(cur, ids):
    cur.execute("SELECT id, topic_key, readrank_selected, left(quote_text,60) qt, "
                "left(deidentified_text,60) dt, left(editor_note,60) en "
                "FROM essentials.quotes WHERE id = ANY(%s) ORDER BY id", (ids,))
    return {r[0]: r for r in cur.fetchall()}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fixes_file")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    ops = json.loads(open(a.fixes_file).read())
    ids = sorted({op["id"] for op in ops})
    conn = connect(); conn.autocommit = False
    cur = conn.cursor()
    before = _snapshot(cur, ids)
    for op in ops:
        sql, params = build_statement(op)
        cur.execute(sql, params)
    after = _snapshot(cur, ids)
    print("=== DIFF (before → after) ===")
    for i in ids:
        if before[i] != after[i]:
            print(f"[{i}]\n  before: {before[i][1:]}\n  after:  {after[i][1:]}")
    if a.commit:
        conn.commit(); print("*** COMMITTED ***")
    else:
        conn.rollback(); print("*** DRY RUN — ROLLED BACK ***")
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests + a real dry-run**

Run tests: `cd .claude/skills/audit-quotes && ../../../.venv/bin/python -m pytest tests/test_apply_fixes.py -q` → PASS.
Then a real no-op dry-run (empty list): `echo '[]' > /tmp/fx.json && ../../../.venv/bin/python -m scripts.apply_fixes /tmp/fx.json` → prints "DRY RUN — ROLLED BACK".

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/audit-quotes/scripts/apply_fixes.py .claude/skills/audit-quotes/tests/test_apply_fixes.py
git commit -m "feat(audit-quotes): gated fix applier (dry-run/commit) with tests"
```

---

## Task 7: CHECKS.md — catalog + judgment-agent prompt + findings schema

**Files:**
- Create: `.claude/skills/audit-quotes/CHECKS.md`

- [ ] **Step 1: Write CHECKS.md** with these exact sections:

1. **Findings schema** — the JSON object each judgment agent returns per finding, matching `Finding` fields: `check_id, level, principle, severity, fix_class, what, suggested_fix, quote_id, topic_key, race_id, candidate` (agents omit `fix_op`).
2. **Mechanical checks table** (implemented in `checks.py`, listed for humans): `note-missing/section-ref/too-long`, `deid-missing`, `trailing-ellipsis`, `partisan-tell`, `source-tier-4`, `multiple-live`, `not-rankable` — with severity + fix-class.
3. **Judgment checks table** — for each: id, what to look for, severity, fix-class:
   - `not-forward` (quote is record/attack, no forward position) — high — decision-required
   - `is-attack` (attacks a person, not a policy/institution) — high — guided (trim to position) / decision-required
   - `off-question` (doesn't answer the topic's framed question) — high — decision-required
   - `deid-dishonest` (blind paraphrased instead of marked, or leaves a self-ID/named person) — high — guided
   - `note-not-self-contained` (note doesn't state stance alignment / needs the doc to understand) — medium — guided
   - `source-summary` (tier-4 quote is a summarized bullet list, not a verbatim sentence) — high — decision-required
   - `coupling-in-tension` (quote pulls against the candidate's Compass value) — medium — decision-required
4. **Judgment-agent prompt template** — the exact prompt the skill sends per race (or race×topic), embedding: the context bundle JSON, the relevant excerpt of `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` (§1, §4, §5, §7), and "return a JSON array of findings using the schema above; empty array if clean; do not fix, only flag."
5. **Portfolio check** — instructions for the per-race skew pass: compare live-topic coverage per candidate; if one candidate is live on most topics while another is absent from most, emit a `portfolio` finding (`coverage-skew`, medium, decision-required) describing the asymmetry as a signal to investigate.

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/audit-quotes/CHECKS.md
git commit -m "docs(audit-quotes): check catalog, judgment prompt, findings schema"
```

---

## Task 8: SKILL.md — the workflow

**Files:**
- Create: `.claude/skills/audit-quotes/SKILL.md`

- [ ] **Step 1: Write SKILL.md** with YAML frontmatter (`name: audit-quotes`, description triggering on "audit quotes", "review quotes", "check quotes against principles") and this workflow:

1. **Principles + catalog:** read `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` and `CHECKS.md` first.
2. **Resolve scope + confirm:** run `scripts/audit.py` with the user's scope (default all races). Show the printed `SCOPE:` line and the mechanical findings count; **confirm with the user before the judgment fan-out** (state approx. one agent per race).
3. **Judgment fan-out:** for each `context/<race>.json`, dispatch a parallel `Agent`-tool subagent with the Task-7 prompt (bundle + principles excerpt). Each returns a JSON findings array. Aggregate with the mechanical findings.
4. **Portfolio pass:** per race, apply the Task-7 skew instructions over the bundle; append `coverage-skew` findings.
5. **Render report:** merge all findings; write the consolidated report to `docs/audits/<YYYY-MM-DD>-quote-audit[-<scope>].md` (use `report.render`); summarize inline (counts + top races).
6. **Gated fixes, per race:** for each race with mechanical/guided fixes, build a fixes JSON, run `scripts/apply_fixes.py fixes.json` (dry-run), show the diff, and run with `--commit` **only after explicit user OK**. Guided fixes: draft the new text, confirm wording with the user first. List decision-required findings for the user; never auto-apply them.
7. **Non-negotiables** section: read-only until the gated step; every write is dry-run-then-OK; never auto-apply decision-required; report is the primary deliverable.

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/audit-quotes/SKILL.md
git commit -m "docs(audit-quotes): SKILL.md workflow"
```

---

## Task 9: publish-quotes handoff

**Files:**
- Modify: `.claude/skills/publish-quotes/SKILL.md` (Workflow checklist, after the verify step)

- [ ] **Step 1: Add the handoff step** at the end of the `## Workflow` list:

```markdown
- [ ] **Auto-run the audit (handoff).** After `--commit`, run the `audit-quotes` skill scoped to the
      just-inserted ids: `audit-quotes --ids <id1,id2,...> --include-drafts --scope-label "<race> new"`.
      Show the findings before the user selects the live quote in `/admin/readrank-quotes`. Fix
      mechanical/guided findings via the audit's gated flow; surface decision-required ones.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/publish-quotes/SKILL.md
git commit -m "feat(publish-quotes): auto-run audit-quotes on inserted ids"
```

---

## Task 10: End-to-end validation on a real race (no writes)

- [ ] **Step 1: Full mechanical + one judgment agent, CA Governor**

Run: `cd .claude/skills/audit-quotes && ../../../.venv/bin/python -m scripts.audit --candidate "Xavier Becerra" --scope-label "Becerra"` then manually dispatch one judgment agent over a `context/<race>.json` per the SKILL prompt; confirm it returns valid findings JSON and that known issues surface (e.g. the deportation non-verbatim summary as `source-summary`, any note issues).

- [ ] **Step 2: Dry-run a mechanical fix**

Build a one-op fixes file from a real mechanical finding (e.g. a `note-missing`), run `apply_fixes.py` **without** `--commit`, confirm the diff prints and rolls back. Do not commit.

- [ ] **Step 3: Commit any fixes made to the plan/skill during validation**, then stop for review.

---

## Self-Review

- **Spec coverage:** all-races default (Task 5 default) ✓; mechanical pre-pass (Task 2) ✓; Agent fan-out judgment (Tasks 7/8) ✓; portfolio skew (Tasks 7/8) ✓; three-class findings (Task 1 model + catalog) ✓; consolidated report to `docs/audits/...` (Tasks 4/8) ✓; per-race gated dry-run fixes (Tasks 6/8) ✓; stance+spectrum pulled live (Task 3 `fetch_stance`, Task 5 bundles) ✓; source-of-truth = principles doc (Tasks 7/8 read it) ✓; publish-quotes handoff (Task 9) ✓; on-the-record location + venv/DATABASE_URL pattern ✓.
- **Deferred correctly:** edit-history/corrections tables out of scope (spec) — not planned. Portfolio was pulled IN per user (Task 7/8) ✓.
- **Type consistency:** `Finding` fields used identically across `checks.py`, `report.py`, `models.py`. **Fix-class discipline:** only truly deterministic fixes are `mechanical` and carry a `fix_op` the applier auto-runs — currently just `trailing-ellipsis` (`regex_sub`). `note-*`, `deid-missing`, and `partisan-tell` are `guided` (drafted → confirmed → applied via `set_field`), so they carry no auto `fix_op`. `build_statement` op kinds (`set_field`/`regex_sub`/`set_live`) cover both the one mechanical `fix_op` and the guided/decision fixes a human confirms. Detection-by-SQL (a "mechanical check") is a separate axis from fix-class — a SQL-detected finding can still be a guided fix.
- **Open verification flagged in-task:** the `race_candidates` join (Task 3 note) must be confirmed against the live schema during implementation.
