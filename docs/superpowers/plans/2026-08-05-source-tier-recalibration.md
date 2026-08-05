# Source Tier Recalibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-rank the source-tier ladder around questioner independence (town halls → tier 1, partisan-host interviews → tier 3, questionnaires → tier 2), make the triage queue order by it, and draft the Gray/Hearst/Nexstar consent letters — per the approved spec `docs/superpowers/specs/2026-08-05-source-tier-recalibration.md`.

**Architecture:** The canonical ladder text lives in the essentials repo (`docs/QUOTE-CURATION-PRINCIPLES.md` §5); the discovery classifier carries a one-paragraph copy in its prompt (`src/discovery/classify.py`); the GUI orders the pending queue by the classifier's `source_tier_guess`; a one-shot cursor-injected re-classify module brings already-pending rows onto the new semantics. Eval fixtures gain hand-labeled `expected_tier` so tier accuracy is measured (non-gating) alongside the gating relevance metrics.

**Tech Stack:** Python 3 (`.venv/bin/python` — never system python3), pytest, psycopg2, ev-accounts SQL migrations, Markdown docs. Three repos: on-the-record (Tasks 1–7, 10), essentials (Task 8), ev-accounts (Task 9).

**Branches:** on-the-record work on `feat/source-tier-recalibration` (worktree via superpowers:using-git-worktrees); essentials on `docs/quote-curation-questioner-independence`; ev-accounts on `feat/source-outlets-county`.

**Ordering constraint:** Task 1 (baseline eval) MUST run before Task 2 (prompt change) — the regression gate in Task 3 compares against Task 1's numbers. Tasks 4–10 are order-independent after Task 2. Task 11 (ops) runs only after the on-the-record branch merges.

---

## The new ladder (reference for every task)

| Tier | Contents |
|---|---|
| 1 | Debates, candidate forums, town halls — independent moderator, opponents, or citizen questioning |
| 2 | Independent-questioner interviews & Q&A — established news orgs (network/local TV, radio, nonpartisan nonprofit newsrooms), A Starting Point, candidate questionnaires (unedited answers to an independent questioner's fixed questions) |
| 3 | Sympathetic-questioner interviews (partisan/ideological podcasts & web shows; an interview podcast that is not itself a news organization counts here) AND prepared public remarks (stump/rally/launch speeches, floor speeches, testimony) |
| 4 | Candidate-bylined written (op-eds, platform pages) |
| 5 | Hard-excluded: hot-mic, private, secretly-recorded, off-guard "gotcha" (unchanged) |

Rules: prefer 1–2; 3–4 need a justification note; 5 banned. Written-medium rule: any written source at any tier yields only verbatim sentences the candidate wrote. Only-surface rule (per candidate): a tier-3 sympathetic-host interview is never excluded when it's the candidate's only sourceable speech — the justification note says so.

One clarification the plan makes concrete (flagged to Chris at spec approval): the spec's "outlet character undeterminable → default tier 2" applies to **non-podcast** outlets. A host-guest interview podcast/web show that is not itself a news organization defaults to **tier 3** — the format is the signal (candidates pick friendly pods), and the per-candidate only-surface rule is the escape hatch, not a higher tier.

---

### Task 1: Tier-accuracy eval plumbing + `expected_tier` labels + baseline run

**Files:**
- Modify: `src/discovery/eval.py`
- Modify: `tests/test_discovery_eval.py`
- Modify: `tests/fixtures/discovery_eval.jsonl`, `tests/fixtures/discovery_eval_real.jsonl`
- Modify: `scripts/eval_discovery_classifier.py`

- [ ] **Step 1: Write the failing test for `tier_accuracy`**

Append to `tests/test_discovery_eval.py` (match the file's existing import style — it already imports from `src.discovery.eval` and `src.discovery.models`):

```python
def test_tier_accuracy_scores_only_labeled_verdicts():
    from src.discovery.eval import tier_accuracy
    from src.discovery.models import Verdict
    pairs = [
        (1, Verdict(True, 0.9, source_tier_guess=1)),   # correct
        (2, Verdict(True, 0.8, source_tier_guess=3)),   # wrong
        (None, Verdict(True, 0.8, source_tier_guess=2)),  # unlabeled -> skipped
        (3, Verdict(False, 0.0, rejected_reason="no JSON in reply")),  # parse failure -> skipped
        (2, Verdict(True, 0.7, source_tier_guess=None)),  # model gave no tier -> counted wrong
    ]
    out = tier_accuracy(pairs)
    assert out["n"] == 3
    assert out["correct"] == 1
    assert abs(out["accuracy"] - 1 / 3) < 1e-9


def test_tier_accuracy_empty():
    from src.discovery.eval import tier_accuracy
    out = tier_accuracy([])
    assert out == {"n": 0, "correct": 0, "accuracy": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_eval.py -v -k tier_accuracy`
Expected: FAIL with `ImportError: cannot import name 'tier_accuracy'`

- [ ] **Step 3: Implement `tier_accuracy` in `src/discovery/eval.py`**

Append to the file (after `calibration`):

```python
def tier_accuracy(pairs: list) -> dict:
    """pairs = [(expected_tier, Verdict)]. Non-gating ride-along: measures the
    tier ladder judgment on the subset of examples that carry an expected_tier
    label (unlabeled and parse-failure examples are skipped; a labeled example
    where the model returned no tier counts as wrong)."""
    scored = [(exp, v.source_tier_guess) for exp, v in pairs
              if exp is not None and v.rejected_reason is None]
    if not scored:
        return {"n": 0, "correct": 0, "accuracy": None}
    correct = sum(1 for exp, got in scored if got == exp)
    return {"n": len(scored), "correct": correct,
            "accuracy": correct / len(scored)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_eval.py -v`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 5: Label the fixtures with `expected_tier`**

Labels follow the NEW ladder (this is the point: the baseline run in Step 8 will score the OLD prompt against the NEW labels — expect a low baseline tier accuracy; that number is the "before" evidence). `gold_relevant=false` rows get no label (tier is meaningless for irrelevant items).

Run this from the repo root:

```bash
.venv/bin/python - <<'EOF'
import json
from pathlib import Path

# index -> expected_tier under the questioner-independence ladder
SYNTHETIC = {0: 1,  # WISN governor debate
             1: 1,  # LWV Kansas candidate forum
             2: 2,  # KXAN one-on-one interview
             3: 3}  # "What's Next" podcast episode (non-news-org podcast)
REAL = {0: 3,   # WTMJ: Crowley launches bid -> announcement speech (prepared remarks)
        1: 2,   # CBS TEXAS: birth-tourism segment w/ candidate (news-org questioning)
        2: 2,   # CBS TEXAS: Lt. Gov on Talarico (news interview)
        3: 3,   # Fountainhead Forum FF-467 (podcast)
        4: 3,   # No Peasants w/ Sawant & Husseini (podcast)
        # 5: rejected clip -> no label
        6: 3,   # Fountainhead Forum FF-461 (podcast)
        7: 1,   # Civic Media STARS Governor Candidates Town Hall
        8: 3,   # Pulse Check Wisconsin interview (non-news-org podcast)
        9: 3,   # FOX6: Crowley campaign rally (prepared remarks)
        10: 2,  # MS NOW interview (news org)
        11: 3,  # Focus On The Candidates: Etzkorn (candidate-interview web show)
        12: 3,  # No Peasants w/ Nkromo & Whiting (podcast)
        13: 3,  # Focus On The Candidates: Hurley (web show)
        14: 2,  # Channel 3000 FULL INTERVIEW (local news org)
        15: 3}  # Focus On The Candidates: Bell (web show)

for path, labels in [(Path("tests/fixtures/discovery_eval.jsonl"), SYNTHETIC),
                     (Path("tests/fixtures/discovery_eval_real.jsonl"), REAL)]:
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for i, line in enumerate(lines):
        d = json.loads(line)
        if i in labels:
            d["expected_tier"] = labels[i]
        out.append(json.dumps(d, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(path, "labeled:", len(labels))
EOF
```

Expected output: `... discovery_eval.jsonl labeled: 4` and `... discovery_eval_real.jsonl labeled: 15`.

- [ ] **Step 6: Wire tier accuracy into `scripts/eval_discovery_classifier.py`**

Three edits, matching the existing route-accuracy pattern:

(a) Import — extend the existing eval import line:

```python
from src.discovery.eval import calibration, classify_outcome, summarize, tier_accuracy  # noqa: E402
```

(b) Inside `main()`, collect tier pairs. After `route_stats_by_model = []` add:

```python
    tier_stats_by_model = []
```

Inside the per-model loop, after `route_total = {stem: 0 for stem in fixture_stems}` add:

```python
        tier_pairs = {stem: [] for stem in fixture_stems}
```

Inside the per-example loop, after the route-accuracy block (`if expected_route is not None: ...`) add:

```python
            tier_pairs[ex["_fixture"]].append((ex.get("expected_tier"), verdict))
```

After the per-model loop body (next to `route_stats_by_model.append(...)`) add:

```python
        tier_stats_by_model.append((model, tier_pairs))
```

(c) At the bottom, after the route-accuracy print loop, add:

```python
    for model, tier_pairs in tier_stats_by_model:
        all_pairs = [p for stem in fixture_stems for p in tier_pairs[stem]]
        for label, pairs_subset in ([(stem, tier_pairs[stem]) for stem in fixture_stems]
                                    + [("combined", all_pairs)]):
            ta = tier_accuracy(pairs_subset)
            if ta["accuracy"] is not None:
                print(f"tier_accuracy ({model} · {label}): "
                      f"{ta['correct']}/{ta['n']} = {ta['accuracy']:.2f}  (non-gating)")
```

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — same count as main plus the 2 new tests (`test_eval_harness_loading.py` reads the fixture files; confirm it still passes with the new key present).

- [ ] **Step 8: Record the BASELINE eval (old prompt, new labels)**

Requires `.env.local` API keys (auto-loaded via `gui.env.load_env_local`). Run:

```bash
.venv/bin/python scripts/eval_discovery_classifier.py | tee eval-baseline-pre-retier.txt
```

Expected: recall/precision/Brier near the ship baseline (0.84 / 1.00 / 0.117 combined; the fixture set may have grown past 24 — whatever it prints IS the baseline). `tier_accuracy` lines print — expect LOW (the old prompt tiers town halls 3 and podcasts 2). Keep `eval-baseline-pre-retier.txt` for Task 3; do not commit it.

- [ ] **Step 9: Commit**

```bash
git add src/discovery/eval.py tests/test_discovery_eval.py scripts/eval_discovery_classifier.py tests/fixtures/discovery_eval.jsonl tests/fixtures/discovery_eval_real.jsonl
git commit -m "feat(discovery): tier-accuracy eval ride-along + expected_tier fixture labels"
```

---

### Task 2: Retiered classifier prompt + `questionnaire` kind

**Files:**
- Modify: `src/discovery/classify.py:23-24` (ALLOWED_KINDS), `:44-66` (prompt blocks)
- Test: `tests/test_discovery_classify.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discovery_classify.py`:

```python
def test_parse_verdict_accepts_questionnaire_kind():
    v = classify.parse_verdict(
        '{"relevant": true, "confidence": 0.8, "event_kind": "questionnaire",'
        ' "source_tier": 2, "route": "quote_source", "why": "unedited answers"}')
    assert v.event_kind_guess == "questionnaire"
    assert v.source_tier_guess == 2
    assert v.route == "quote_source"


def test_build_prompt_tiers_by_questioner_independence():
    prompt = classify.build_prompt(_item(), race_label="TX Senate",
                                   roster_names=["Maria Delgado"])
    # town halls are tier 1, not tier 3
    assert "town hall (independent moderator, opponents, or citizen questioning)" in prompt
    # prepared remarks live in tier 3 alongside sympathetic-questioner interviews
    assert "stump speech" in prompt.split("4 =")[0].split("3 =")[1]
    # questionnaire is an emittable kind
    assert "questionnaire" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_classify.py -v -k "questionnaire or independence"`
Expected: FAIL — `event_kind_guess` is `None` (kind not in ALLOWED_KINDS) and the prompt assertions miss.

- [ ] **Step 3: Implement — ALLOWED_KINDS and the prompt**

In `src/discovery/classify.py` change:

```python
ALLOWED_KINDS = {"debate", "forum", "news_clip", "press_conference",
                 "podcast", "community_meeting", "questionnaire", "other"}
```

Replace the entire block from `Source tiers:` through the closing `"""` of `_PROMPT_TEMPLATE` (currently lines 44–66) with:

```python
Source tiers — rank by QUESTIONER INDEPENDENCE (how hard is it for the candidate
to only say what they came to say):
1 = debate, candidate forum, or town hall (independent moderator, opponents, or citizen questioning);
2 = interview or Q&A with an independent questioner: established news organizations
(network/local TV, radio, nonpartisan nonprofit newsrooms), A Starting Point videos,
or a published candidate questionnaire carrying the candidate's own unedited answers;
3 = sympathetic-questioner interview (partisan/ideological podcast or web show,
party-aligned host, candidate-friendly platform — an interview podcast or web show
that is not itself a news organization belongs here) OR prepared public remarks
(stump speech, rally, campaign launch, floor speech, testimony);
4 = candidate-bylined written (op-ed, platform page).
If the outlet's character is genuinely undeterminable and the item is not a
podcast/web show, use tier 2.
"original_vs_clip": "original" = the full event / substantial segment where the
candidate speaks at length; "clip" = a short excerpt or a package about them.
Set "relevant" to true ONLY for original sources of the candidates' own words —
i.e. when original_vs_clip is "original". News packages ABOUT candidates, campaign
ads, and highlight/clip compilations are relevant=false even when the candidate
appears or is quoted in them.
If a captions or article-page excerpt is provided, judge DISCOURSE SHAPE: sustained
first-person policy speech and moderator/Q&A signatures suggest an original event;
third-person anchor narration with soundbites suggests a news package. Do not guess
who is speaking — only whether candidate speech is present at length.

For "web page" items: Q&A-shaped text — an interviewer/panel back-and-forth, or a
per-candidate questionnaire page with the candidate's unedited answers to fixed
questions — is the most valuable quote_source; use event_kind "questionnaire" for
the questionnaire shape. Route "quote_source" unless the page clearly hosts the
full event recording (full video embed or full podcast episode) — then "ingest".

Respond with JSON only:
{{"relevant": true/false, "confidence": 0.0-1.0,
  "candidates_present": ["names from the tracked list that appear"],
  "event_kind": "debate|forum|news_clip|press_conference|podcast|community_meeting|questionnaire|other",
  "source_tier": 1-4, "original_vs_clip": "original|clip",
  "route": "ingest|quote_source",
  "why": "one sentence citing your strongest evidence"}}"""
```

(Everything from `"original_vs_clip":` down is unchanged except the two lines noted: the web-page paragraph gains the Q&A-shape guidance, and the JSON contract's `event_kind` enum gains `questionnaire`.)

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_discovery_classify.py -v`
Expected: PASS (all — the pre-existing prompt tests assert page_kind and roster, not tier text).

- [ ] **Step 5: Full suite + commit**

Run: `.venv/bin/python -m pytest tests/ -q` — expected PASS.

```bash
git add src/discovery/classify.py tests/test_discovery_classify.py
git commit -m "feat(discovery): retier classifier by questioner independence + questionnaire kind"
```

---

### Task 3: Post-change eval run + regression gate

**Files:** none modified — this is a measurement gate.

- [ ] **Step 1: Run the eval with the new prompt**

```bash
.venv/bin/python scripts/eval_discovery_classifier.py | tee eval-post-retier.txt
```

- [ ] **Step 2: Apply the gate**

Compare `eval-post-retier.txt` against `eval-baseline-pre-retier.txt` (Task 1 Step 8), combined rows:

- **Gate (must hold): recall, precision each within 0.05 absolute of baseline; Brier within +0.05.** If violated: iterate on the Task 2 prompt wording (the tier block only — do not touch the relevance rules), re-run, re-gate. If two iterations fail, STOP and surface to Chris with both outputs.
- **Ride-along (report, don't gate): tier_accuracy combined** — expect a large jump vs baseline (baseline mislabels every town hall and podcast by construction). Record both numbers in the PR description.

- [ ] **Step 3: Commit nothing; carry both txt files into the PR description, then delete them**

```bash
rm eval-baseline-pre-retier.txt eval-post-retier.txt
```

---

### Task 4: Triage queue orders by tier

**Files:**
- Modify: `gui/discovery.py:101-105` (`pending_rows`)
- Test: `tests/test_gui_discovery.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_discovery.py`:

```python
def test_pending_order_ranks_tier_before_confidence():
    from gui import discovery
    order = discovery._PENDING_ORDER
    assert "election_date asc" in order
    tier_pos = order.index("source_tier_guess asc")
    conf_pos = order.index("confidence desc")
    assert tier_pos < conf_pos
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v -k pending_order`
Expected: FAIL with `AttributeError: module 'gui.discovery' has no attribute '_PENDING_ORDER'`

- [ ] **Step 3: Implement**

In `gui/discovery.py`, add a module constant directly below `_SELECT` and use it in `pending_rows`:

```python
_PENDING_ORDER = """
    where d.status = 'pending'
    order by e.election_date asc nulls last,
             d.source_tier_guess asc nulls last,
             d.confidence desc nulls last, d.created_at desc
"""
```

and in `pending_rows` replace the inline SQL tail:

```python
                cur.execute(_SELECT + _PENDING_ORDER)
```

- [ ] **Step 4: Run the tests, then commit**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v` — expected PASS.

```bash
git add gui/discovery.py tests/test_gui_discovery.py
git commit -m "feat(gui): pending discovery queue orders by source tier before confidence"
```

---

### Task 5: One-shot re-classify of pending rows

**Files:**
- Create: `src/discovery/reclassify.py`
- Create: `scripts/reclassify_pending.py`
- Test: `tests/test_discovery_reclassify.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_discovery_reclassify.py`:

```python
"""Cursor- and provider-injected tests for the one-shot pending re-classify."""
from src.discovery import reclassify


class _FakeProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, prompt, *, max_tokens, temperature, system=None):
        self.prompts.append(prompt)
        return self.replies.pop(0)


class _FakeCursor:
    def __init__(self, rows_by_query=None):
        self.executed = []
        self._rows = rows_by_query or {}

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._rows.get("fetchall", [])


_ROW = ("11111111-1111-1111-1111-111111111111",  # id
        "https://www.youtube.com/watch?v=abc12345678", "STARS Town Hall",
        "Candidates take citizen questions", "Civic Media", 5400,
        "2026-07-30", "22222222-2222-2222-2222-222222222222",
        "WI Governor (primary)", 3)  # race_label, old_tier


def test_reclassify_row_updates_tier_kind_confidence_why_only():
    provider = _FakeProvider(['{"relevant": true, "confidence": 0.9,'
                              ' "event_kind": "forum", "source_tier": 1,'
                              ' "original_vs_clip": "original",'
                              ' "route": "ingest", "why": "citizen questions"}'])
    cur = _FakeCursor({"fetchall": [("Alice Example",), ("Bob Sample",)]})
    old_tier, new_tier = reclassify.reclassify_row(cur, provider, _ROW)
    assert (old_tier, new_tier) == (3, 1)
    update_sql = cur.executed[-1][0]
    assert "set source_tier_guess" in update_sql
    for untouched in ("status", "route", "discovered_via"):
        assert untouched not in update_sql
    assert "Alice Example" in provider.prompts[0]


def test_reclassify_row_skips_on_parse_failure():
    provider = _FakeProvider(["not json"])
    cur = _FakeCursor({"fetchall": []})
    old_tier, new_tier = reclassify.reclassify_row(cur, provider, _ROW)
    assert (old_tier, new_tier) == (3, None)
    assert all("update" not in sql for sql, _ in cur.executed)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_reclassify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.discovery.reclassify'`

- [ ] **Step 3: Implement `src/discovery/reclassify.py`**

```python
"""One-shot re-classify of pending discovered_sources rows after a tier
prompt change (spec 2026-08-05-source-tier-recalibration). Cursor- and
provider-injected so it composes in one transaction (house pattern, db.py).

Updates ONLY the classifier-guess fields (source_tier_guess,
event_kind_guess, confidence, why) — never status, route, or provenance.
Metadata-only: the fetch-time captions/page peek is not re-run."""
from __future__ import annotations

from src.discovery.classify import classify_item
from src.discovery.models import RawItem


def fetch_pending(cur) -> list:
    cur.execute("""
        select d.id::text, d.url, d.title, d.description_snippet,
               d.channel_name, d.duration_seconds, d.published_at::text,
               d.race_id::text, p.race_label, d.source_tier_guess
        from essentials.discovered_sources d
        left join essentials.readrank_race_pipeline p on p.race_id = d.race_id
        where d.status = 'pending'
        order by d.created_at
    """)
    return cur.fetchall()


def roster_for_race(cur, race_id: str) -> list:
    cur.execute("""
        select rc.full_name from essentials.race_candidates rc
        where rc.race_id = %s::uuid and rc.full_name is not null
          and coalesce(rc.candidate_status, 'active')
              not in ('withdrawn', 'removed')
        order by rc.full_name
    """, (race_id,))
    return [r[0] for r in cur.fetchall()]


def reclassify_row(cur, provider, row) -> tuple:
    """Returns (old_tier, new_tier); new_tier is None when the verdict
    failed to parse (row left untouched)."""
    (row_id, url, title, desc, channel, duration, published,
     race_id, race_label, old_tier) = row
    roster = roster_for_race(cur, race_id) if race_id else []
    item = RawItem(url=url, title=title, description=desc,
                   channel_name=channel, duration_seconds=duration,
                   published_at=published, via="search")
    verdict = classify_item(provider, item,
                            race_label=race_label or "(unknown race)",
                            roster_names=roster, peek_fetcher=None)
    if verdict.rejected_reason is not None:
        return old_tier, None
    cur.execute("""
        update essentials.discovered_sources
        set source_tier_guess = %s, event_kind_guess = %s,
            confidence = %s, why = %s
        where id = %s::uuid
    """, (verdict.source_tier_guess, verdict.event_kind_guess,
          verdict.confidence, verdict.why, row_id))
    return old_tier, verdict.source_tier_guess
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_reclassify.py -v`
Expected: PASS

- [ ] **Step 5: Write the thin script `scripts/reclassify_pending.py`**

```python
"""One-shot: re-run the tier classifier over pending discovered_sources rows.

Run once after the 2026-08-05 tier recalibration deploys, so the triage
queue sorts coherently under the new ladder. Idempotent; harmless to re-run.

Usage:
  .venv/bin/python scripts/reclassify_pending.py [--dry-run] [--limit N]
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()

from src import config  # noqa: E402
from src.discovery import db, reclassify  # noqa: E402
from src.llm_providers import get_provider  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="classify and report, but roll back all updates")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N pending rows")
    args = ap.parse_args()
    provider = get_provider(config.DISCOVERY_MODEL_ACTIVE)
    conn = db.connect()
    moves = Counter()
    try:
        with conn.cursor() as cur:
            rows = reclassify.fetch_pending(cur)
            if args.limit is not None:
                rows = rows[: args.limit]
            print(f"pending rows: {len(rows)}")
            for row in rows:
                old_tier, new_tier = reclassify.reclassify_row(cur, provider, row)
                title = (row[2] or "")[:60]
                if new_tier is None:
                    moves["parse_failure"] += 1
                    print(f"  PARSE-FAIL tier={old_tier} {title!r}")
                else:
                    moves[f"{old_tier}->{new_tier}"] += 1
                    marker = " " if old_tier == new_tier else "*"
                    print(f"  {marker} {old_tier} -> {new_tier} {title!r}")
        if args.dry_run:
            conn.rollback()
            print("DRY RUN — rolled back")
        else:
            conn.commit()
    finally:
        conn.close()
    print("moves:", dict(sorted(moves.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Full suite + commit**

Run: `.venv/bin/python -m pytest tests/ -q` — expected PASS.

```bash
git add src/discovery/reclassify.py scripts/reclassify_pending.py tests/test_discovery_reclassify.py
git commit -m "feat(discovery): one-shot pending re-classify for the retiered prompt"
```

---

### Task 6: Runbook edits (tier order, questionnaire hunt, county packs)

**Files:**
- Modify: `docs/runbooks/source-discovery.md`

- [ ] **Step 1: Update the gap-filler prompt's tier order and add the questionnaire/only-surface lines**

In the `## Agent gap-filler (zero-source alarm)` blockquote, replace:

```
> Find original sources of the candidates' own spoken words for RACE_LABEL
> (election ELECTION_DATE). Tier order: debates/forums, news interviews,
> prepared remarks, candidate-bylined written. Search the open web, local TV
```

with:

```
> Find original sources of the candidates' own spoken words for RACE_LABEL
> (election ELECTION_DATE). Tier order (questioner independence):
> debates/forums/town halls; independent-press interviews and candidate
> questionnaires (unedited answers — Vote411, LWV chapters, WyoFile-style
> outlet questionnaire pages); partisan-podcast interviews and prepared
> remarks; candidate-bylined written. Search the open web, local TV
```

And after the A Starting Point paragraph (before the `> Never C-SPAN.` line), insert:

```
>
> For minor-party and independent candidates, explicitly search podcast
> interviews: a sympathetic-host podcast is tier 3, but it is never excluded
> when it is that candidate's only sourceable speech (per-candidate rule,
> QUOTE-CURATION-PRINCIPLES §5) — note "only sourceable speech" in `why`.
```

- [ ] **Step 2: Add the county source packs section**

Insert a new section immediately after the `## Outlet packs (rolling seed)` section:

```markdown
## County source packs (on demand)

State/federal outlet packs do not reach county-level races. When a county race
enters the pipeline queue (or trips the zero-source alarm), run a pack agent
scoped to the county — do NOT build county packs speculatively.

Order of value:
1. **LWV chapter channels first** — county/local League chapters record forums
   and post them on YouTube; they are also debate co-producers whose copies
   sidestep barred-chain station sites.
2. **Hyperlocal news second** — county papers, local digital newsrooms. The
   registration ToS gate applies with extra care: several hyperlocals are
   already barred (Upslope Media's County17 / Oil City / CapCity, County 10,
   Sweetwater Now — see the chain scoreboard in the source-discovery memory).
3. County civic orgs (chambers of commerce, community foundations) that host
   candidate forums.

Set `state` AND `county` on every outlet row (`county` is the bare county name,
e.g. `Monroe` with `state='IN'`). Everything else matches the state-pack recipe
above (register both surfaces, ToS verdict in `notes`, active on insert).
```

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/source-discovery.md
git commit -m "docs(runbook): questioner-independence tier order, questionnaire hunting, county packs"
```

---

### Task 7: Skill wording — race-pipeline SKILL + audit-quotes CHECKS

**Files:**
- Modify: `.claude/skills/race-pipeline/SKILL.md:63-66`
- Modify: `.claude/skills/audit-quotes/CHECKS.md:56, 330, 395, 427`

- [ ] **Step 1: race-pipeline SKILL.md — rewrite the hierarchy sentence**

Replace:

```
Per candidate, work DOWN the source hierarchy (QUOTE-CURATION-PRINCIPLES §5): 1 debates &
forums, 2 news interviews, 3 prepared remarks, 4 candidate-bylined written (verbatim
sentences only + justification note). Tier 5 (hot-mic/gotcha) is banned. Curate against
```

with:

```
Per candidate, work DOWN the source hierarchy (QUOTE-CURATION-PRINCIPLES §5, ranked by
questioner independence): 1 debates, forums & town halls; 2 independent-press interviews &
candidate questionnaires; 3 partisan-host interviews & prepared remarks; 4 candidate-bylined
written. Tiers 3–4 need a justification note; any WRITTEN source at any tier yields verbatim
sentences only. Tier 5 (hot-mic/gotcha) is banned. A tier-3 podcast is never excluded when it
is a candidate's only sourceable speech — the justification note says so. Curate against
```

- [ ] **Step 2: audit-quotes CHECKS.md — four wording edits**

(a) Line 56, the checks table row — replace:

```
| `source-tier-4` | quote | prefer tier 1–2 spoken sources | medium | decision-required |
```

with:

```
| `source-tier-4` | quote | prefer tier 1–2 sources (questioner-independent) | medium | decision-required |
```

(b) Line 330, the `source-summary` row — replace:

```
| `source-summary` | A written / tier-4 source (op-ed, platform page) is rendered as a curator-summarized bullet list or paraphrase rather than a verbatim sentence actually written by the candidate. | high | decision-required |
```

with:

```
| `source-summary` | A written source at ANY tier (op-ed, platform page, questionnaire answer) is rendered as a curator-summarized bullet list or paraphrase rather than a verbatim sentence actually written by the candidate. | high | decision-required |
```

(c) Line ~395, the judgment-prompt bullet — replace:

```
- **Verbatim, not summary.** For written/lower-tier sources (op-eds, platform pages), the
```

with:

```
- **Verbatim, not summary.** For written sources at any tier (op-eds, platform pages, questionnaire answers), the
```

(d) Line ~427, the findings list — replace:

```
- `source-summary` — a written/tier-4 quote is a summarized bullet list, not a verbatim
```

with:

```
- `source-summary` — a written-source quote is a summarized bullet list, not a verbatim
```

- [ ] **Step 3: Run the audit-quotes skill tests (they exist)**

Run: `.venv/bin/python -m pytest .claude/skills/audit-quotes/tests/ -q 2>/dev/null || .venv/bin/python -m pytest tests/ -q`
Expected: PASS (the check IDs did not change, only descriptions).

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/race-pipeline/SKILL.md .claude/skills/audit-quotes/CHECKS.md
git commit -m "docs(skills): tier ladder by questioner independence; written-medium verbatim rule"
```

---

### Task 8: essentials §5 rewrite (cross-repo)

**Files (in `/Users/chrisandrews/Documents/GitHub/essentials`):**
- Modify: `docs/QUOTE-CURATION-PRINCIPLES.md` §5 (lines ~265-287)

- [ ] **Step 1: Branch in the essentials repo**

```bash
cd /Users/chrisandrews/Documents/GitHub/essentials
git fetch origin && git switch -c docs/quote-curation-questioner-independence origin/main 2>/dev/null || git switch -c docs/quote-curation-questioner-independence origin/master
```

(Use whichever default branch exists; check with `git remote show origin | grep HEAD`.)

- [ ] **Step 2: Replace the hierarchy block**

Replace (current lines 265-277):

```markdown
**Hierarchy (best → worst):**

1. **Debates & candidate forums** — spoken, on-record, probed.
2. **News interviews** — spoken, on-record, questioned.
3. **Prepared public remarks** — stump/floor speeches, testimony (spoken, unprobed).
4. **Candidate-bylined written** — op-eds, official platform, *only if clearly the candidate's
   own words, quoted as a **verbatim sentence** from the source — never a curator-summarized
   bullet list* (e.g. "Support DACA, oppose Muslim ban and family separation" is a summary, not a
   quote).
5. **Hard-excluded — not merely deprioritized:** hot-mic, private, secretly-recorded, or clearly
   off-the-cuff "gotcha" remarks. Using off-guard speech is the manufactured-drama we reject and
   it corrodes trust. **Do not use.**
```

with:

```markdown
**Hierarchy (best → worst), ranked by QUESTIONER INDEPENDENCE — how hard is it for the
candidate to only say what they came to say:**

1. **Debates, candidate forums & town halls** — spoken, on-record, probed by an independent
   moderator, opponents, or citizens.
2. **Independent-questioner interviews & Q&A** — interviews by established news organizations
   (network/local TV, radio, nonpartisan nonprofit newsrooms); A Starting Point videos
   (*caveat: curated questions, zero follow-up — structured self-presentation; prefer a
   genuine press interview when both exist*); and **candidate questionnaires** — the
   candidate's own unedited answers to an independent questioner's fixed questions
   (LWV/Vote411, WyoFile-style outlet pages) — the best available *text* source.
3. **Sympathetic-questioner interviews & prepared remarks** — partisan/ideological podcasts
   and web shows, party-aligned hosts, candidate-friendly platforms; stump/rally/launch
   speeches, floor speeches, testimony. *Per-candidate exception: a sympathetic-host
   interview is never excluded when it is the candidate's only sourceable speech — the
   justification note says so.*
4. **Candidate-bylined written** — op-eds, official platform pages.
5. **Hard-excluded — not merely deprioritized:** hot-mic, private, secretly-recorded, or clearly
   off-the-cuff "gotcha" remarks. Using off-guard speech is the manufactured-drama we reject and
   it corrodes trust. **Do not use.**

- **Written-medium rule (any tier):** a written source — questionnaire answer, op-ed,
  platform page — yields quotes *only as verbatim sentences the candidate actually wrote*,
  never a curator-summarized bullet list (e.g. "Support DACA, oppose Muslim ban and family
  separation" is a summary, not a quote).
```

- [ ] **Step 3: Check the §5 back-references still read correctly**

Run: `grep -n "tier" docs/QUOTE-CURATION-PRINCIPLES.md`
Verify: "strongly prefer tiers 1–2; allow 3–4 *with a justification note*" (line ~278) — still correct under the new rungs, no edit. "slot by speaking context (tier 2–3)" (social-media bullet, line ~281) — still correct. "tier 3–4 sources" (line ~335 defense list) — still correct. If any reference now contradicts the new rungs, fix it in this commit and note it in the commit message.

- [ ] **Step 4: Commit + push + PR**

```bash
git add docs/QUOTE-CURATION-PRINCIPLES.md
git commit -m "docs: §5 source hierarchy ranked by questioner independence

Town halls join tier 1; partisan-host interviews join prepared remarks in
tier 3 (with the per-candidate only-surface exception); A Starting Point and
candidate questionnaires placed at tier 2; the verbatim-sentence rule becomes
a written-medium rule at every tier. Spec: on-the-record
docs/superpowers/specs/2026-08-05-source-tier-recalibration.md

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push -u origin docs/quote-curation-questioner-independence
gh pr create --title "docs: §5 source hierarchy ranked by questioner independence" --body "See on-the-record spec docs/superpowers/specs/2026-08-05-source-tier-recalibration.md. Companion to the on-the-record retier PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

### Task 9: ev-accounts migration — `source_outlets.county` (cross-repo)

**Files (in `/Users/chrisandrews/Documents/GitHub/ev-accounts`):**
- Create: `backend/migrations/<NEXT>_source_outlets_county.sql`

- [ ] **Step 1: Fetch and find the next migration number (THE NUMBERING TRAP — never skip)**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git fetch origin && git switch -c feat/source-outlets-county origin/master
node backend/scripts/check-migration-numbers.mjs
```

The check script reports the highest number in use (prod is at ≥1556 as of 2026-08-04 even if the local checkout looks older — that is WHY the fetch comes first). Use highest+1 as `<NEXT>` below.

- [ ] **Step 2: Write the migration**

Create `backend/migrations/<NEXT>_source_outlets_county.sql`:

```sql
-- Source tier recalibration (on-the-record spec 2026-08-05): county-level
-- races need county source packs; make county coverage queryable.
-- Bare county name, e.g. 'Monroe' with state='IN'. Pack agents set it at
-- outlet registration; discovery code treats it as pass-through metadata.
ALTER TABLE essentials.source_outlets ADD COLUMN county text NULL;
```

- [ ] **Step 3: Re-run the number check, commit, push, PR**

```bash
node backend/scripts/check-migration-numbers.mjs
git add backend/migrations/
git commit -m "feat: source_outlets.county for county-level source packs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push -u origin feat/source-outlets-county
gh pr create --title "feat: source_outlets.county for county source packs" --body "One nullable column; see on-the-record spec docs/superpowers/specs/2026-08-05-source-tier-recalibration.md §County column.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 4: Apply to prod (Chris or a session with DB access)**

Additive nullable column — safe to apply any time relative to code deploys (nothing selects it). Apply per house flow (Supabase MCP `apply_migration`, or `psql "$DATABASE_URL" -f backend/migrations/<NEXT>_source_outlets_county.sql` — DATABASE_URL must use the IPv4 pooler host). Verify:

```sql
select column_name from information_schema.columns
where table_schema = 'essentials' and table_name = 'source_outlets'
  and column_name = 'county';
```

---

### Task 10: Consent letters (Gray primary + Hearst/Nexstar variants)

**Files:**
- Create: `docs/outreach/2026-08-gray-media-consent-letter.md`
- Create: `docs/outreach/2026-08-hearst-consent-letter.md`
- Create: `docs/outreach/2026-08-nexstar-consent-letter.md`

These are DRAFTS for Chris to review, edit, and send himself — nothing in this task contacts anyone.

- [ ] **Step 1: Create `docs/outreach/2026-08-gray-media-consent-letter.md`**

```markdown
# Draft — Gray Media written-consent request (NOT SENT; Chris reviews & sends)

To: Gray Media, Inc. — Legal / Digital Licensing
From: Chris Andrews, Empowered Vote (candrews@empowered.vote)
Re: Request for prior express written consent under your Terms of Use — non-commercial civic use

Dear Gray Media legal team,

I run Empowered Vote (empowered.vote), a free, non-commercial voter-education
project. We help voters compare candidates in their own words: we locate public
recordings of candidate debates, forums, town halls, and interviews, transcribe
them, attribute speakers, and publish short verbatim quotes — each deep-linked
back to the original recording on the publisher's own page or player.

Your Terms of Use bar AI/ML tools from accessing, copying, storing, or
reproducing GLM Content without prior express written consent. This letter
requests that consent, narrowly:

**What we ask consent for.** Automated access, copying, storage, and processing
of election-related candidate-speech content published on Gray station sites —
candidate debates, forums, town halls, interviews, and candidate Q&A pages —
using AI-assisted transcription and speaker-attribution tools, solely to produce
attributed verbatim candidate quotes for voter education.

**What we expressly do NOT do.**
- No AI model training on your content, of any kind.
- No republication of full video or articles — we publish short quotes with
  attribution and a deep link to your page/player at the cited timestamp.
- No paywall or authentication circumvention; we honor robots.txt and poll only
  public syndication endpoints, with per-domain politeness delays.
- No commercial use: Empowered Vote has no advertising, no donations, and no
  paid tier.

**What Gray gets.** Attribution on every quote and a deep link that sends voters
to your stations' own players and pages for the full context.

Local stations are often the only professional record of down-ballot candidate
speech; consent would directly improve what voters in Gray markets can learn
about their own elections. I'm happy to adjust scope, add conditions, or sign a
short-form agreement reflecting the above.

Thank you for considering this.

Chris Andrews
Empowered Vote — empowered.vote
candrews@empowered.vote
```

- [ ] **Step 2: Create the Hearst variant `docs/outreach/2026-08-hearst-consent-letter.md`**

Same letter with two substitutions — the addressee block (`To: Hearst Television, Inc. — Legal / Digital Licensing`, `Re: Written consent for non-commercial civic use of station content`) and the ToS paragraph replaced by:

```markdown
Your terms prohibit use of station content for AI training. Empowered Vote does
not train AI models on any content, yours included — our tools only transcribe
and attribute candidates' public statements. We are writing to put that beyond
doubt: we request your written consent for the narrow, non-commercial civic use
described below, so there is no ambiguity about the boundary between prohibited
training and the transcription-for-attribution we actually do.
```

All other sections (the four-bullet "do NOT do" list, "What Hearst gets," closing) identical to the Gray letter with the chain name substituted.

- [ ] **Step 3: Create the Nexstar variant `docs/outreach/2026-08-nexstar-consent-letter.md`**

Identical to the Hearst variant with `Nexstar Media Group, Inc.` as addressee and chain-name substitutions.

- [ ] **Step 4: Commit**

```bash
git add docs/outreach/
git commit -m "docs(outreach): draft civic-use consent letters for Gray, Hearst, Nexstar (not sent)"
```

---

### Task 11: Post-merge ops (after the on-the-record PR merges)

No files — operational checklist. Do not start before merge.

- [ ] **Step 1: Dry-run the re-classify against prod**

```bash
.venv/bin/python scripts/reclassify_pending.py --dry-run --limit 20
```

Expected: 20 rows print with `old -> new` tiers; town-hall-titled items move toward 1, podcast items toward 3; `DRY RUN — rolled back`. Sanity-check the moves before the real run.

- [ ] **Step 2: Real run**

```bash
.venv/bin/python scripts/reclassify_pending.py | tee reclassify-2026-08.log
```

Record the `moves:` summary (it is the queue-consistency evidence for spec success criterion 2).

- [ ] **Step 3: Verify the GUI ordering**

Open the Discovery tab; confirm pending items group by election date, then tier ascending. A tier-1 town hall for the nearest election sits above every tier-3 podcast for the same race.

- [ ] **Step 4: Re-run the eval after the next triage harvest**

After the next `harvest_discovery_verdicts.py` run grows the real fixture set, add `expected_tier` labels to the NEW rows (harvested rows arrive unlabeled — human triage does not record tier) and re-run `scripts/eval_discovery_classifier.py`. Tier accuracy on fresh, never-seen examples is the evidence that decides the spec's deferred outlet-annotation question.

---

## Success-criteria map (spec → tasks)

| Spec criterion | Task(s) |
|---|---|
| 1. Eval regression gate + tier accuracy reported | 1, 2, 3 |
| 2. Queue ordered by tier + re-classify run with logged diff | 4, 5, 11 |
| 3. §5 / SKILL / CHECKS / runbook all state one ladder | 6, 7, 8 |
| 4. `source_outlets.county` live + county-pack runbook section | 6, 9 |
| 5. Three consent letters drafted for review | 10 |

## Post-review amendments (Task 2, 2026-08-05)

Code review of Task 2 (retiered classifier prompt + `questionnaire` kind) approved "with fixes." The following were applied after the initial commit, as a second commit on the same branch:

- **Web-page paragraph gained an "original" carve-out for questionnaires.** Review finding: the preamble frames relevance around the candidates' *spoken* words and defines "original" as "the full event... where the candidate speaks at length," so a model could coherently judge a questionnaire page `original_vs_clip: "clip"` → `relevant: false` → auto-filtered, even though the page is exactly the quote_source the tier-2 rung was written for. Added a clause instructing the model to treat a page carrying the candidate's substantial unedited answers as "original" (the answers are the candidate's own words, written not spoken).
- **`test_build_prompt_tiers_by_questioner_independence` tightened**: added `assert "4 =" in prompt` before the split-based tier-3 assertion (so a missing rung 4 can't silently pass), and replaced the loose `assert "questionnaire" in prompt` with `assert "community_meeting|questionnaire|other" in prompt`, which pins the JSON `event_kind` enum rather than just the tier-block prose.
- **One synthetic questionnaire fixture added** to `tests/fixtures/discovery_eval.jsonl` (`gold_relevant: true`, `expected_tier: 2`), a WyoFile-style "we asked every candidate the same eight questions" item with no `source_key`, so the eval harness routes it through the "web page" lane and exercises the new original-vs-clip clause. (Not run here — API credits are out; the eval run is handled separately.)

The code in `src/discovery/classify.py` and `tests/test_discovery_classify.py` is authoritative over Task 2's verbatim prompt/test blocks quoted earlier in this plan; where they differ, the shipped code reflects this amendment.

### Task 5 (2026-08-05)

Code review of Task 5 (one-shot re-classify of pending rows) approved "with fixes." One Important defect, inherited from this plan's own verbatim code block: `reclassify_row`'s guard only checked `verdict.rejected_reason`, so a valid, parseable verdict that carried no usable tier (missing or out-of-range `source_tier`) would still execute the UPDATE — writing `source_tier_guess = NULL` — while the function's return value `(old_tier, None)` told the caller (and the script's `parse_failure` counter/print line) that the row had been left untouched. Fixed by widening the guard to `if verdict.rejected_reason is not None or verdict.source_tier_guess is None: return old_tier, None`, so a no-tier verdict now skips the update exactly like a parse failure, and the return value stays truthful. The script's counter/label was renamed `parse_failure` → `skipped_no_verdict` (print line: `SKIPPED (no usable verdict) tier=... `) since it now covers both failure modes. A third test, `test_reclassify_row_skips_when_verdict_has_no_tier`, pins the new guard with a parseable-but-tierless verdict.

The code in `src/discovery/reclassify.py`, `scripts/reclassify_pending.py`, and `tests/test_discovery_reclassify.py` is authoritative over Task 5's verbatim code blocks quoted earlier in this plan; where they differ, the shipped code reflects this amendment.

### Task 7 (2026-08-05)

Review approved Task 7 (race-pipeline SKILL + audit-quotes CHECKS wording) with one fast-follow: this plan's Task 7 file list named only the two doc files and missed the code side of the `source-tier-4` check. `.claude/skills/audit-quotes/scripts/checks.py`'s `check_source_tier` hard-codes the runtime `Finding.principle` string curators actually see, and it still read `"prefer tier 1-2 spoken sources"` — contradicting the reworded CHECKS.md row and implying spoken-only, which the new ladder explicitly forbids (tier 2 now includes written questionnaire answers). Updated the string to `"prefer tier 1-2 sources (questioner-independent)"`, matching the CHECKS.md table row verbatim. No test in `.claude/skills/audit-quotes/tests/` pinned the old string (`test_source_tier_campaign_site_flagged` asserts only `check_id == "source-tier-4"`, not the principle text), so no test changes were needed.

The code in `.claude/skills/audit-quotes/scripts/checks.py` is authoritative over Task 7's principle-string wording; where it differs from a verbatim block quoted earlier in this plan, the shipped code reflects this amendment.
