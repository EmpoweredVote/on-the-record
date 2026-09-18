# Slice 2 Phase 4 — Hub-lane comparable-source recall eval — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the Slice-2 comparable-source recall eval into a repo-committed regression test that measures how many of the hand-labeled comparable common-question sources the shipped hub lane actually surfaces per race.

**Architecture:** A pure, offline, unit-tested scorer (`src/discovery/hub_recall_eval.py`) computes recall/precision from URL sets; a manual online runner (`scripts/eval_hub_recall.py`) drives the real hub lane (`hubs_for_race` → `hub_search.raw_items_for_race` via live Tavily → `classify.classify_item` via live OpenRouter) over N runs and prints the numbers. Two committed fixtures supply the ground truth and a reproducible hub-registry snapshot.

**Tech Stack:** Python 3, existing `src/discovery/*` modules, `psycopg2` (only for optional `--hubs db`), Tavily + OpenRouter (live, via existing `src.discovery.web_search` / `src.llm_providers`). Tests use `pytest` (offline only).

Spec: `docs/superpowers/specs/2026-09-18-slice2-phase4-hub-recall-eval-design.md`.

## Global Constraints

- **Interpreter:** always `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python` (the `.venv` lives only in the main checkout; the system `python3` lacks deps). Run every command from the worktree cwd so `src`/`gui` resolve to the worktree code under test.
- **The runner is NOT a pytest test.** It makes live Tavily + OpenRouter calls. Only the pure scorer + the two fixtures get pytest coverage, and those tests must stay fully offline (conftest deletes `DATABASE_URL`; make no network/DB call in any test).
- **Keys** (already present, non-empty, in the main checkout's `.env.local`): `TAVILY_API_KEY` (retrieval), `OPENROUTER_API_KEY` (classify). There is no `.env` file. When running the online harness or a DB snapshot from the worktree, load the main checkout's env, e.g. `set -a; . /Users/chrisandrews/Documents/GitHub/on-the-record/.env.local; set +a` before the command, or pass `--env-file`.
- **Discovery model** is `config.DISCOVERY_MODEL_ACTIVE` (`"deepseek"`). Confidence floor `config.DISCOVERY_CONFIDENCE_FLOOR` (0.30). Hub budget `config.DISCOVERY_HUB_BUDGET` (6). Do not hardcode these values — read them from `config`.
- **Mirror production accept logic exactly.** An item counts as "verified/accepted" only if it passes `prefilter_item` AND `classify_item` returns `rejected_reason is None and relevant and confidence >= DISCOVERY_CONFIDENCE_FLOOR` (a `prior_cycle`-flagged verdict is still `relevant`, so it counts). This mirrors `src/discovery/engine.py::process`.
- **Commit style:** end each commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Pure recall scorer + offline unit tests

**Files:**
- Create: `src/discovery/hub_recall_eval.py`
- Test: `tests/test_hub_recall_eval.py`

**Interfaces:**
- Produces (used by Task 4):
  - `normalize_url(url: str) -> str`
  - `registrable_domain(url_or_host: str) -> str`
  - `source_urls(source: dict) -> list[str]`
  - `source_in_urls(source: dict, urls: list[str]) -> bool`
  - `is_addressable(source: dict, hub_domains) -> bool`
  - `score_run(gt_sources: list[dict], hub_domains, found_urls: list[str], accepted_urls: list[str]) -> list[dict]` — each dict `{"id","addressable","retrieved","accepted"}`
  - `recalls_from_per_source(rows: list[dict]) -> dict`
  - `precision(gt_sources: list[dict], accepted_urls: list[str]) -> dict`
  - `majority(count: int, n_runs: int) -> bool`
  - `majority_per_source(records: list[dict], n_runs: int) -> list[dict]` — records `{"id","addressable","retrieved_count","accepted_count"}`
- A `source` dict has at least `{"id": str, "url": str}` and optional `"accept_urls": list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hub_recall_eval.py
"""Offline unit tests for the pure hub-recall scorer. No network, no DB."""
from src.discovery import hub_recall_eval as hre


def test_normalize_url_strips_scheme_www_query_fragment_and_trailing_slash():
    assert hre.normalize_url("https://www.Ballotpedia.org/Karen_Bass/") == "ballotpedia.org/karen_bass"
    assert hre.normalize_url("http://laist.com/x?y=1#z") == "laist.com/x"
    assert hre.normalize_url("ballotpedia.org/Nithya_Raman") == "ballotpedia.org/nithya_raman"
    assert hre.normalize_url("") == ""


def test_registrable_domain_reduces_subdomains():
    assert hre.registrable_domain("https://news.azpm.org/p/x") == "azpm.org"
    assert hre.registrable_domain("https://onyourballot.vote411.org/x") == "vote411.org"
    assert hre.registrable_domain("ballotpedia.org") == "ballotpedia.org"
    assert hre.registrable_domain("https://princetontx.new.swagit.com/videos/1") == "swagit.com"
    assert hre.registrable_domain("https://vote.utah.gov/a") == "utah.gov"


def test_source_in_urls_matches_primary_and_accept_urls_ignoring_query():
    src = {"id": "s0", "url": "https://ballotpedia.org/Karen_Bass",
           "accept_urls": ["https://ballotpedia.org/Nithya_Raman"]}
    assert hre.source_in_urls(src, ["https://ballotpedia.org/Karen_Bass?x=1"]) is True
    assert hre.source_in_urls(src, ["http://www.ballotpedia.org/Nithya_Raman/"]) is True
    assert hre.source_in_urls(src, ["https://ballotpedia.org/Someone_Else"]) is False
    assert hre.source_in_urls(src, []) is False


def test_is_addressable_uses_registrable_domain_on_both_sides():
    src = {"id": "s0", "url": "https://news.azpm.org/p/x"}
    assert hre.is_addressable(src, ["azpm.org"]) is True          # exact registrable
    assert hre.is_addressable(src, ["news.azpm.org"]) is True     # subdomain hub -> same registrable
    assert hre.is_addressable(src, ["ballotpedia.org"]) is False
    # addressable via an accept_url on a hub domain
    src2 = {"id": "s1", "url": "https://nbclosangeles.com/x",
            "accept_urls": ["https://laist.com/y"]}
    assert hre.is_addressable(src2, ["laist.com"]) is True


def test_score_run_flags_each_source():
    gt = [
        {"id": "a", "url": "https://ballotpedia.org/A"},
        {"id": "b", "url": "https://news.azpm.org/b"},
        {"id": "c", "url": "https://vote411.org/c"},
    ]
    rows = hre.score_run(gt, hub_domains=["ballotpedia.org", "azpm.org"],
                         found_urls=["https://ballotpedia.org/A", "https://news.azpm.org/b"],
                         accepted_urls=["https://ballotpedia.org/A"])
    by_id = {r["id"]: r for r in rows}
    assert by_id["a"] == {"id": "a", "addressable": True, "retrieved": True, "accepted": True}
    assert by_id["b"] == {"id": "b", "addressable": True, "retrieved": True, "accepted": False}
    assert by_id["c"] == {"id": "c", "addressable": False, "retrieved": False, "accepted": False}


def test_recalls_from_per_source_math():
    rows = [
        {"id": "a", "addressable": True, "retrieved": True, "accepted": True},
        {"id": "b", "addressable": True, "retrieved": True, "accepted": False},
        {"id": "c", "addressable": False, "retrieved": True, "accepted": True},
        {"id": "d", "addressable": False, "retrieved": False, "accepted": False},
    ]
    r = hre.recalls_from_per_source(rows)
    assert r["n_gt"] == 4 and r["n_addressable"] == 2
    assert r["addressable_recall"] == 0.5           # 1 of 2 addressable accepted
    assert r["overall_recall"] == 0.5               # 2 of 4 accepted
    assert r["retrieval_recall_overall"] == 0.75    # 3 of 4 retrieved
    assert r["retrieval_recall_addressable"] == 1.0 # 2 of 2 addressable retrieved


def test_recalls_none_when_no_addressable():
    rows = [{"id": "a", "addressable": False, "retrieved": False, "accepted": False}]
    r = hre.recalls_from_per_source(rows)
    assert r["addressable_recall"] is None
    assert r["retrieval_recall_addressable"] is None
    assert r["overall_recall"] == 0.0


def test_precision():
    gt = [{"id": "a", "url": "https://ballotpedia.org/A"}]
    p = hre.precision(gt, ["https://ballotpedia.org/A", "https://spam.com/x"])
    assert p == {"n_accepted": 2, "n_matched": 1, "precision": 0.5}
    assert hre.precision(gt, [])["precision"] is None


def test_majority_is_strict():
    assert hre.majority(2, 3) is True
    assert hre.majority(1, 3) is False
    assert hre.majority(2, 4) is False   # tie is not a majority
    assert hre.majority(3, 4) is True


def test_majority_per_source_rolls_up_counts():
    recs = [{"id": "a", "addressable": True, "retrieved_count": 3, "accepted_count": 2}]
    out = hre.majority_per_source(recs, n_runs=3)
    assert out == [{"id": "a", "addressable": True, "retrieved": True, "accepted": True}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_hub_recall_eval.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.discovery.hub_recall_eval'`.

- [ ] **Step 3: Write the scorer**

```python
# src/discovery/hub_recall_eval.py
"""Pure scoring for the hub-lane comparable-source recall eval (Slice 2 Phase 4).

No network, no filesystem, no DB — importable and unit-tested offline. The online
runner (scripts/eval_hub_recall.py) supplies the found/accepted URLs and the
per-race hub domains; this module turns them into recall/precision numbers
against the hand-labeled ground truth.

Per race (GT = comparable ground-truth sources):
  A (addressable) = GT whose registrable domain is a scoped_search hub domain
                    applicable to the race (pointer-only hubs are dropped by the
                    runner before their domains reach here).
  R (retrieved)   = GT matched by any raw (pre-classify) found URL.
  V (verified)    = GT matched by any classifier-accepted URL.
Headline = addressable recall = |V ∩ A| / |A|.
"""
from __future__ import annotations

import re

# Multi-label public suffixes; kept tiny on purpose (the ground truth is all
# plain .gov/.org/.com). Extend only with evidence.
_MULTI_SUFFIXES = {"co.uk", "org.uk", "gov.uk", "com.au"}
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://")


def normalize_url(url: str) -> str:
    """Canonical match form: lowercased, scheme/query/fragment/leading-www./
    trailing-slash removed. Path case is lowercased too — applied to both sides,
    so consistent for matching."""
    u = (url or "").strip().lower()
    u = u.split("#", 1)[0].split("?", 1)[0]
    u = _SCHEME.sub("", u)
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


def registrable_domain(url_or_host: str) -> str:
    """Best-effort eTLD+1 from a URL or bare host."""
    u = normalize_url(url_or_host)
    host = u.split("/", 1)[0].split(":", 1)[0]
    labels = [x for x in host.split(".") if x]
    if len(labels) <= 2:
        return ".".join(labels)
    last2 = ".".join(labels[-2:])
    if last2 in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return last2


def source_urls(source: dict) -> list:
    return [source["url"], *(source.get("accept_urls") or [])]


def source_in_urls(source: dict, urls: list) -> bool:
    targets = {normalize_url(u) for u in source_urls(source)}
    return any(normalize_url(u) in targets for u in urls)


def is_addressable(source: dict, hub_domains) -> bool:
    hub_regs = {registrable_domain(d) for d in hub_domains if d}
    return any(registrable_domain(u) in hub_regs for u in source_urls(source))


def score_run(gt_sources: list, hub_domains, found_urls: list, accepted_urls: list) -> list:
    return [{
        "id": s["id"],
        "addressable": is_addressable(s, hub_domains),
        "retrieved": source_in_urls(s, found_urls),
        "accepted": source_in_urls(s, accepted_urls),
    } for s in gt_sources]


def _ratio(num: int, den: int):
    return (num / den) if den else None


def recalls_from_per_source(rows: list) -> dict:
    addr = [r for r in rows if r["addressable"]]
    return {
        "n_gt": len(rows),
        "n_addressable": len(addr),
        "n_retrieved": sum(1 for r in rows if r["retrieved"]),
        "n_verified": sum(1 for r in rows if r["accepted"]),
        "addressable_recall": _ratio(sum(1 for r in addr if r["accepted"]), len(addr)),
        "overall_recall": _ratio(sum(1 for r in rows if r["accepted"]), len(rows)),
        "retrieval_recall_overall": _ratio(sum(1 for r in rows if r["retrieved"]), len(rows)),
        "retrieval_recall_addressable": _ratio(sum(1 for r in addr if r["retrieved"]), len(addr)),
    }


def precision(gt_sources: list, accepted_urls: list) -> dict:
    n = len(accepted_urls)
    if not n:
        return {"n_accepted": 0, "n_matched": 0, "precision": None}
    matched = sum(1 for u in accepted_urls if any(source_in_urls(s, [u]) for s in gt_sources))
    return {"n_accepted": n, "n_matched": matched, "precision": matched / n}


def majority(count: int, n_runs: int) -> bool:
    return 2 * count > n_runs


def majority_per_source(records: list, n_runs: int) -> list:
    return [{
        "id": r["id"], "addressable": r["addressable"],
        "retrieved": majority(r["retrieved_count"], n_runs),
        "accepted": majority(r["accepted_count"], n_runs),
    } for r in records]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_hub_recall_eval.py -q`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add src/discovery/hub_recall_eval.py tests/test_hub_recall_eval.py
git commit -m "feat(discovery): pure recall scorer for the hub-lane comparable-source eval (Slice 2 Phase 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Ground-truth fixture (curated from the spike) + schema test

**Files:**
- Create: `tests/fixtures/hub_recall_ground_truth.json`
- Test: `tests/test_hub_recall_fixture.py`
- Read (source data): `docs/superpowers/spikes/2026-09-17-slice2-comparable-hubs/bakeoff/ground_truth.json`

**Interfaces:**
- Produces (consumed by Task 4 + Task 3 test): a JSON object `{"_about": str, "races": [ ... ]}`. Each race: `{"race","state","race_label","year","candidates":[...], "sources":[{"id","type","tier","tos","exists","url","accept_urls":[...]}, ...]}`. `race_label` contains the cycle year (the classifier reads the year from it). `id` is globally unique.

- [ ] **Step 1: Write the failing schema test**

```python
# tests/test_hub_recall_fixture.py
"""Offline schema checks for the hub-recall ground-truth fixture."""
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures/hub_recall_ground_truth.json"


def test_ground_truth_fixture_shape():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    races = data["races"]
    assert len(races) == 8
    seen_ids = set()
    for race in races:
        for key in ("race", "state", "race_label", "year", "candidates", "sources"):
            assert race.get(key), f"{race.get('race')} missing {key}"
        assert race["year"] in race["race_label"], f"{race['race']} label lacks year"
        assert len(race["state"]) == 2
        assert race["sources"], f"{race['race']} has no sources"
        for s in race["sources"]:
            assert s["id"] and s["url"], f"{race['race']} source missing id/url"
            assert s["id"] not in seen_ids, f"duplicate id {s['id']}"
            seen_ids.add(s["id"])
            assert isinstance(s.get("accept_urls", []), list)
            assert s["url"].startswith("http")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_hub_recall_fixture.py -q`
Expected: FAIL (`FileNotFoundError` — the fixture does not exist yet).

- [ ] **Step 3: Generate the fixture from the spike ground truth**

Run this one-shot generator (paste into the worktree shell). It copies the mechanical fields verbatim from the spike file and applies the curated enrichment (race labels, years, `accept_urls` for the same source at an alternate URL — drawn from the spike's per-race notes):

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python - <<'PY'
import json, pathlib
SRC = pathlib.Path("docs/superpowers/spikes/2026-09-17-slice2-comparable-hubs/bakeoff/ground_truth.json")
OUT = pathlib.Path("tests/fixtures/hub_recall_ground_truth.json")
src = json.loads(SRC.read_text())

ENRICH = {
  "az-governor": {"race_label": "Arizona Governor (AZ, 2026)", "year": "2026", "accept_urls": {
    "https://ballotpedia.org/Arizona_gubernatorial_and_lieutenant_gubernatorial_election,_2026":
      ["https://ballotpedia.org/Teri_Hourihan"]}},
  "az-house-06": {"race_label": "U.S. Representative Arizona District 6 (AZ, 2026)", "year": "2026", "accept_urls": {
    "https://ballotpedia.org/Juan_Ciscomani":
      ["https://ballotpedia.org/JoAnna_Mendoza", "https://ballotpedia.org/Jereme_Peters"]}},
  "az-mine-inspector": {"race_label": "Arizona Mine Inspector (AZ, 2026)", "year": "2026", "accept_urls": {
    "https://www.youtube.com/watch?v=DWBatUCY0_0":
      ["https://news.azpm.org/p/news-articles/2026/8/31/231019-debate-arizona-mine-inspector/"]}},
  "la-mayor": {"race_label": "Los Angeles Mayor (CA, 2026)", "year": "2026", "accept_urls": {
    "https://www.nbclosangeles.com/news/local/bass-raman-to-face-off-in-first-debate-of-mayoral-runoff/3931316/":
      ["https://laist.com/news/politics/bass-and-raman-face-off-in-first-mayoral-debate-ahead-of-the-general-election"],
    "https://laist.com/news/politics/2026-election-california-primary-karen-bass-los-angeles-mayor-transcript":
      ["https://laist.com/news/politics/2026-election-california-primary-nithya-raman-los-angeles-mayor-transcript"],
    "https://ballotpedia.org/Karen_Bass": ["https://ballotpedia.org/Nithya_Raman"]}},
  "bend-mayor-or": {"race_label": "Bend Mayor (OR, 2026)", "year": "2026", "accept_urls": {}},
  "austin-cc-d5": {"race_label": "Austin City Council District 5 (TX, 2026)", "year": "2026", "accept_urls": {}},
  "ut-sboe-14": {"race_label": "Utah State Board of Education District 14 (UT, 2026)", "year": "2026", "accept_urls": {
    "https://ballotpedia.org/Nichole_Isom": ["https://ballotpedia.org/Linda_Hanks"],
    "https://ivoterguide.com/candidate/92072/race/25429/election/1383":
      ["https://ivoterguide.com/candidate/80649/race/25429/election/1383"]}},
  "princeton-council-tx": {"race_label": "Princeton City Council Seat 4 (TX, 2026)", "year": "2026", "accept_urls": {
    "https://web.archive.org/web/20260604152434/https://princetonherald.com/2026/05/30/council-runoff-candidates-meet-in-forum/":
      ["https://princetonherald.com/2026/05/30/council-runoff-candidates-meet-in-forum/"],
    "https://ballotpedia.org/Jaisen_Rutledge_(Princeton_City_Council_Seat_4,_Texas,_candidate_2026)":
      ["https://ballotpedia.org/Jan_Goria_(Princeton_City_Council_Seat_4,_Texas,_candidate_2026)"]}},
}

out = {"_about": ("Hub-lane comparable-source recall ground truth (Slice 2 Phase 4). "
                  "Derived from the 2026-09-17 spike bakeoff/ground_truth.json: only sources "
                  "marked exists yes/partial with a URL are kept. accept_urls lists the SAME "
                  "source at an alternate URL (e.g. a debate carried by two outlets, a live page "
                  "vs its Wayback copy, or a per-candidate Ballotpedia page). Labels are a "
                  "2026-09-17 snapshot; some 'partial' sources were scheduled-not-yet-aired, which "
                  "is fine for a fixed recall baseline. Regenerate by re-running the generator in "
                  "docs/superpowers/plans/2026-09-18-slice2-phase4-hub-recall-eval.md Task 2."),
       "races": []}
for race in src:
    e = ENRICH[race["race"]]
    sources, i = [], 0
    for s in race["sources"]:
        if s.get("exists") not in ("yes", "partial") or not s.get("url"):
            continue
        sources.append({
            "id": f"{race['race']}-s{i}",
            "type": s["type"], "tier": s.get("tier"), "tos": s.get("tos"),
            "exists": s["exists"], "url": s["url"],
            "accept_urls": e["accept_urls"].get(s["url"], []),
        })
        i += 1
    out["races"].append({
        "race": race["race"], "state": race["state"],
        "race_label": e["race_label"], "year": e["year"],
        "candidates": race["candidates"], "sources": sources,
    })
OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
print(f"wrote {OUT} — {len(out['races'])} races, "
      f"{sum(len(r['sources']) for r in out['races'])} sources")
PY
```

Expected output: `wrote tests/fixtures/hub_recall_ground_truth.json — 8 races, 27 sources`.

- [ ] **Step 4: Run the schema test to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_hub_recall_fixture.py -q`
Expected: PASS. If it fails on the source count, open the generated JSON and confirm every spike source had `exists` in {yes, partial} with a URL (they do as of 2026-09-17).

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/hub_recall_ground_truth.json tests/test_hub_recall_fixture.py
git commit -m "feat(discovery): curated ground-truth fixture for the hub-recall eval (Slice 2 Phase 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Committed hub-registry snapshot + offline load test

**Files:**
- Create: `tests/fixtures/hub_recall_hubs_snapshot.json`
- Test: `tests/test_hub_recall_hubs_snapshot.py`

**Interfaces:**
- Produces (consumed by Task 4): a JSON object `{"hubs": [ {source_hubs row}, ... ]}` where each row carries the `essentials.source_hubs` columns (`name, scope, state, kind, poll_method, domain, query_template, tos_bucket, active, added_via, notes`; `id` optional). Loadable into `src.discovery.hubs.Hub`.

- [ ] **Step 1: Write the failing load test**

```python
# tests/test_hub_recall_hubs_snapshot.py
"""Offline validation of the committed hub-registry snapshot."""
import json
from pathlib import Path

from src.discovery.hubs import Hub, hubs_for_race

SNAP = Path(__file__).resolve().parent / "fixtures/hub_recall_hubs_snapshot.json"
_FIELDS = {"id", "name", "scope", "state", "kind", "poll_method", "domain",
           "query_template", "tos_bucket", "active", "added_via", "notes"}


def _load():
    rows = json.loads(SNAP.read_text(encoding="utf-8"))["hubs"]
    return [Hub(**{k: v for k, v in r.items() if k in _FIELDS}) for r in rows]


def test_snapshot_loads_and_has_ballotpedia_scoped_hub():
    hubs = _load()
    assert len(hubs) >= 10
    bp = [h for h in hubs if (h.domain or "").endswith("ballotpedia.org")]
    assert bp, "no ballotpedia hub in snapshot"
    assert any(h.poll_method == "scoped_search" for h in bp)


def test_snapshot_resolves_for_a_state_race():
    hubs = _load()
    applicable = hubs_for_race(hubs, state="CA")
    assert applicable, "no applicable hubs for CA"
    assert all(h.poll_method == "scoped_search" for h in applicable)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_hub_recall_hubs_snapshot.py -q`
Expected: FAIL (`FileNotFoundError`).

- [ ] **Step 3: Capture the snapshot from the live registry**

Preferred — dump the live `essentials.source_hubs` (needs `DATABASE_URL`, which is in the main `.env.local`):

```bash
set -a; . /Users/chrisandrews/Documents/GitHub/on-the-record/.env.local; set +a
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python - <<'PY' > tests/fixtures/hub_recall_hubs_snapshot.json
import json, sys
sys.path.insert(0, ".")
from src.discovery import db, hubs
conn = db.connect()
try:
    rows = hubs.load_hubs(conn.cursor())
finally:
    conn.close()
print(json.dumps({"hubs": [h.__dict__ for h in rows]}, indent=2))
PY
```

Then sanity-check the row count and that Ballotpedia is present:

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python - <<'PY'
import json
d = json.load(open("tests/fixtures/hub_recall_hubs_snapshot.json"))
print("rows:", len(d["hubs"]))
print("ballotpedia:", [h for h in d["hubs"] if "ballotpedia" in (h.get("domain") or "")])
PY
```

Expected: ~17 rows (the seed: 3 global, 10 per-state, 4 local_type), with a Ballotpedia row whose `poll_method` is `scoped_search`.

**Fallback if the DB is unreachable:** read the seed `INSERT` from the ev-accounts migration `~/Documents/GitHub/ev-accounts/backend/migrations/1869_source_hubs.sql` (steward slot 1869) and hand-write the same rows into the JSON, one object per seeded hub, using the column names in `_FIELDS`.

- [ ] **Step 4: Run the load test to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_hub_recall_hubs_snapshot.py -q`
Expected: PASS. If `test_snapshot_resolves_for_a_state_race` fails, confirm the snapshot includes at least one `scoped_search` global hub (Ballotpedia) — `hubs_for_race` always includes global hubs, so a non-empty result is guaranteed once Ballotpedia is present.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/hub_recall_hubs_snapshot.json tests/test_hub_recall_hubs_snapshot.py
git commit -m "feat(discovery): committed seed hub-registry snapshot for the hub-recall eval (Slice 2 Phase 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: The online runner

**Files:**
- Create: `scripts/eval_hub_recall.py`

**Interfaces:**
- Consumes: `src.discovery.hub_recall_eval` (Task 1), the two fixtures (Tasks 2–3), and the shipped lane: `hubs.Hub`, `hubs.hubs_for_race`, `hubs.load_hubs`, `hub_search.raw_items_for_race`, `hub_search._is_pointer_only`, `classify.classify_item`, `prefilter.prefilter_item`, `feeds.fetch_page_text`, `llm_providers.get_provider`, `config`, `gui.env.load_env_local`, `discovery.db.connect`.
- Produces: the executable eval (no importable API relied on by later tasks; Task 5 only runs it and edits its docstring baseline block).

- [ ] **Step 1: Write the runner**

```python
#!/usr/bin/env python3
"""Hub-lane comparable-source recall eval (Slice 2 Phase 4).

Manual ONLINE harness (live Tavily + OpenRouter) — NOT a pytest test. Runs the
shipped hub lane for each ground-truth race and reports recall of the comparable
common-question sources the ground truth says exist:

  hubs.hubs_for_race -> hub_search.raw_items_for_race (Tavily)
      -> prefilter_item -> classify.classify_item (OpenRouter + page peek)

Reported per race and pooled (micro-averaged) over --runs (majority vote per
source):
  * addressable recall (headline) = found+verified / GT a scoped_search hub domain reaches
  * overall recall (context)      = found+verified / all GT
  * retrieval-only recall (debug) = retrieved by Tavily / GT   (pre-classify)

Usage (repo root; keys from .env.local):
  .venv/bin/python scripts/eval_hub_recall.py [--races SLUG ...] [--runs N]
      [--budget N] [--no-classify] [--hubs snapshot|db] [--print-hubs] [--env-file PATH]

The metric is noisy — use --runs N and treat a change as real only when it beats
the per-run spread this harness prints (use --runs 5 for a tuning decision).

Baseline: NOT YET MEASURED — filled by Task 5 of the plan.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gui.env import load_env_local  # noqa: E402
from src import config  # noqa: E402
from src.discovery import hub_recall_eval as hre  # noqa: E402
from src.discovery import hub_search, hubs  # noqa: E402
from src.discovery.classify import classify_item  # noqa: E402
from src.discovery.feeds import fetch_page_text  # noqa: E402
from src.discovery.prefilter import prefilter_item  # noqa: E402
from src.llm_providers import get_provider  # noqa: E402

GROUND_TRUTH = REPO_ROOT / "tests/fixtures/hub_recall_ground_truth.json"
HUBS_SNAPSHOT = REPO_ROOT / "tests/fixtures/hub_recall_hubs_snapshot.json"
_HUB_FIELDS = {"id", "name", "scope", "state", "kind", "poll_method", "domain",
               "query_template", "tos_bucket", "active", "added_via", "notes"}


def load_ground_truth(path: Path = GROUND_TRUTH) -> list:
    return json.loads(path.read_text(encoding="utf-8"))["races"]


def load_hubs_from_snapshot(path: Path = HUBS_SNAPSHOT) -> list:
    rows = json.loads(path.read_text(encoding="utf-8"))["hubs"]
    return [hubs.Hub(**{k: v for k, v in r.items() if k in _HUB_FIELDS}) for r in rows]


def load_hubs_from_db() -> list:
    from src.discovery import db
    conn = db.connect()
    try:
        return hubs.load_hubs(conn.cursor())
    finally:
        conn.close()


def _peek(url: str):
    try:
        return fetch_page_text(url) or None
    except Exception:  # noqa: BLE001 — the peek is optional; classify proceeds without
        return None


def _accepted(verdict) -> bool:
    return (verdict.rejected_reason is None and verdict.relevant
            and verdict.confidence >= config.DISCOVERY_CONFIDENCE_FLOOR)


def run_race(race: dict, all_hubs: list, provider, *, budget: int, do_classify: bool):
    """One race, one pass. Returns (hub_domains, found_urls, accepted_urls)."""
    applicable = hubs.hubs_for_race(all_hubs, state=race.get("state"))
    hub_domains = [h.domain for h in applicable
                   if h.domain and not hub_search._is_pointer_only(h)]
    items = hub_search.raw_items_for_race(
        applicable, candidates=race["candidates"],
        locality=race["race_label"], year=race.get("year"), budget=budget)
    found_urls = [it.url for it in items]
    accepted_urls = []
    if do_classify:
        for it in items:
            pf = prefilter_item(it.title, it.description, it.duration_seconds, race["candidates"])
            if not pf.passed:
                continue
            v = classify_item(provider, it, race_label=race["race_label"],
                              roster_names=race["candidates"], peek_fetcher=_peek)
            if _accepted(v):
                accepted_urls.append(it.url)
    return hub_domains, found_urls, accepted_urls


def _fmt(x):
    return "  n/a" if x is None else f"{x:.2f}"


def _headline(rec: dict, do_classify: bool):
    return rec["addressable_recall"] if do_classify else rec["retrieval_recall_addressable"]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--races", nargs="+", default=None, help="limit to these race slugs")
    p.add_argument("--runs", type=int, default=3, help="passes per race; majority vote per source")
    p.add_argument("--budget", type=int, default=config.DISCOVERY_HUB_BUDGET,
                   help="scoped searches per race (default config.DISCOVERY_HUB_BUDGET)")
    p.add_argument("--no-classify", action="store_true", help="retrieval only (skips OpenRouter)")
    p.add_argument("--hubs", choices=["snapshot", "db"], default="snapshot",
                   help="registry source (default: committed snapshot)")
    p.add_argument("--print-hubs", action="store_true", help="print the loaded hubs as JSON and exit")
    p.add_argument("--env-file", default=None, help="path to .env.local (worktree runs: point at the main checkout)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    load_env_local(Path(args.env_file) if args.env_file else None)

    all_hubs = load_hubs_from_db() if args.hubs == "db" else load_hubs_from_snapshot()
    if args.print_hubs:
        print(json.dumps({"hubs": [h.__dict__ for h in all_hubs]}, indent=2))
        return 0

    if "TAVILY_API_KEY" not in os.environ:
        print("FATAL: TAVILY_API_KEY not set — the retrieval stage needs it. "
              "Export it or pass --env-file <main>/.env.local.", file=sys.stderr)
        return 2
    do_classify = not args.no_classify
    if do_classify and "OPENROUTER_API_KEY" not in os.environ:
        print("FATAL: OPENROUTER_API_KEY not set — needed to classify. "
              "Export it, pass --env-file, or use --no-classify.", file=sys.stderr)
        return 2

    races = load_ground_truth()
    if args.races:
        want = set(args.races)
        races = [r for r in races if r["race"] in want]
        if not races:
            print(f"FATAL: no ground-truth race matched {sorted(want)}", file=sys.stderr)
            return 2
    provider = get_provider(config.DISCOVERY_MODEL_ACTIVE) if do_classify else None

    print(f"model={config.DISCOVERY_MODEL_ACTIVE if do_classify else '(none)'} "
          f"runs={args.runs} budget={args.budget} races={len(races)} "
          f"hubs={args.hubs} classify={do_classify}\n")

    # acc[race_slug][source_id] = {"addressable","retrieved_count","accepted_count"}
    acc: dict = {}
    per_run_headline = []
    precision_matched = precision_total = 0  # pooled over runs (accepted-item quality)
    for run_i in range(args.runs):
        run_rows = []
        for race in races:
            hub_domains, found, accepted = run_race(
                race, all_hubs, provider, budget=args.budget, do_classify=do_classify)
            rows = hre.score_run(race["sources"], hub_domains, found, accepted)
            run_rows.extend(rows)
            prec = hre.precision(race["sources"], accepted)
            precision_matched += prec["n_matched"]
            precision_total += prec["n_accepted"]
            slot = acc.setdefault(race["race"], {})
            for r in rows:
                cell = slot.setdefault(r["id"], {"addressable": r["addressable"],
                                                 "retrieved_count": 0, "accepted_count": 0})
                cell["addressable"] = r["addressable"]
                cell["retrieved_count"] += int(r["retrieved"])
                cell["accepted_count"] += int(r["accepted"])
        hl = _headline(hre.recalls_from_per_source(run_rows), do_classify)
        per_run_headline.append(hl)
        print(f"run {run_i + 1}/{args.runs}: headline recall = {_fmt(hl)}")

    print("\n| race | addr | overall | retr | n(addr/gt) |")
    print("|---|---|---|---|---|")
    all_majority = []
    for race in races:
        recs = [{"id": sid, **cell} for sid, cell in acc[race["race"]].items()]
        maj = hre.majority_per_source(recs, args.runs)
        all_majority.extend(maj)
        rr = hre.recalls_from_per_source(maj)
        print(f"| {race['race']} | {_fmt(rr['addressable_recall'])} | {_fmt(rr['overall_recall'])} "
              f"| {_fmt(rr['retrieval_recall_overall'])} | {rr['n_addressable']}/{rr['n_gt']} |")

    pooled = hre.recalls_from_per_source(all_majority)
    hv = [h for h in per_run_headline if h is not None]
    spread = (f"{min(hv):.2f}..{max(hv):.2f} (median {statistics.median(hv):.2f})"
              if hv else "n/a")
    print("\n== POOLED (majority vote over runs, micro-averaged over races) ==")
    print(f"addressable recall (headline): {_fmt(pooled['addressable_recall'])}  "
          f"[{pooled['n_verified']}∩A / {pooled['n_addressable']}]")
    print(f"overall recall:                {_fmt(pooled['overall_recall'])}  "
          f"[{pooled['n_verified']} / {pooled['n_gt']}]")
    print(f"retrieval-only recall:         {_fmt(pooled['retrieval_recall_overall'])}  "
          f"[{pooled['n_retrieved']} / {pooled['n_gt']}]")
    pooled_prec = (precision_matched / precision_total) if precision_total else None
    print(f"precision (pooled over runs):  {_fmt(pooled_prec)}  "
          f"[{precision_matched} / {precision_total}]")
    print(f"per-run headline spread:       {spread}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify it imports and `--print-hubs` works offline (no network)**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python scripts/eval_hub_recall.py --hubs snapshot --print-hubs`
Expected: prints the snapshot hubs as JSON and exits 0. (No Tavily/OpenRouter/DB touched.)

- [ ] **Step 3: Verify the missing-key guard**

Run: `env -u TAVILY_API_KEY /Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python scripts/eval_hub_recall.py --races la-mayor --env-file /dev/null`
Expected: prints `FATAL: TAVILY_API_KEY not set ...` and exits non-zero (2). (This proves the guard without spending any API calls; `--env-file /dev/null` stops `.env.local` from supplying the key.)

- [ ] **Step 4: Commit**

```bash
git add scripts/eval_hub_recall.py
git commit -m "feat(discovery): online hub-lane recall eval runner (Slice 2 Phase 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Live baseline run + record it

**Files:**
- Modify: `scripts/eval_hub_recall.py` (the `Baseline:` line in the module docstring)

**Interfaces:**
- Consumes: the finished runner + fixtures (Tasks 1–4) and live keys.
- Produces: the recorded regression baseline (in the docstring) that future tuning is compared against.

- [ ] **Step 1: Run the full suite offline to confirm nothing regressed**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_hub_recall_eval.py tests/test_hub_recall_fixture.py tests/test_hub_recall_hubs_snapshot.py -q`
Expected: all PASS.

- [ ] **Step 2: Run the live baseline (8 races, 3 runs)**

```bash
set -a; . /Users/chrisandrews/Documents/GitHub/on-the-record/.env.local; set +a
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python scripts/eval_hub_recall.py --runs 3
```
Expected: per-run headline lines, a per-race table, and a POOLED block with the three recalls + the per-run spread. This makes real Tavily + OpenRouter calls (budget 6 × 8 races × 3 runs ≈ up to 144 searches plus classify calls). If it errors on env, confirm the keys loaded (`--print-hubs` still works offline; a live run needs the sourced `.env.local`).

- [ ] **Step 3: Record the baseline in the docstring**

Replace the `Baseline: NOT YET MEASURED — filled by Task 5 of the plan.` line with the measured numbers, e.g.:

```
Baseline (measured 2026-09-18, deepseek, runs=3, snapshot registry): addressable
recall X.XX, overall Y.YY, retrieval-only Z.ZZ; per-run headline spread A.AA..B.BB.
A recall change smaller than that spread (or ~0.10, whichever is larger) is noise;
use --runs 5 for a tuning decision.
```

Use the actual numbers from Step 2. Keep it to a few lines.

- [ ] **Step 4: Commit**

```bash
git add scripts/eval_hub_recall.py
git commit -m "docs(discovery): record hub-recall eval baseline (Slice 2 Phase 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 5: (If keys/DB were unavailable) record the deferral instead**

If a live baseline could not be run this session (no `TAVILY_API_KEY` at run time), leave the docstring `Baseline:` line as `Baseline: pending a Tavily key — run `scripts/eval_hub_recall.py --runs 3` to record.` and note the deferral in the PR/handoff. The harness itself is complete and offline-tested regardless.

---

## Self-Review

**Spec coverage:**
- "Runs the real code path (`hubs_for_race` → `hub_search.raw_items_for_race` → `classify.classify_item`)" → Task 4 `run_race`. ✓
- Three numbers (addressable/overall/retrieval-only) with exact set definitions → Task 1 `recalls_from_per_source`, printed in Task 4. ✓
- "found-and-verified" mirrors production `pending` rule incl. prefilter + prior_cycle-stays-relevant → Task 4 `_accepted` + `prefilter_item`; Global Constraints. ✓
- Deterministic matching + `accept_urls` → Task 1 `source_in_urls`/`normalize_url`; Task 2 fixture. ✓
- Addressable read from the registry-under-test → Task 4 `run_race` builds `hub_domains` from `hubs_for_race`, pointer-only dropped via `_is_pointer_only`. ✓
- Files: `scripts/eval_hub_recall.py`, `src/discovery/hub_recall_eval.py`, `tests/fixtures/hub_recall_ground_truth.json`, `tests/fixtures/hub_recall_hubs_snapshot.json`, `tests/test_hub_recall_eval.py` → Tasks 1–4 (plus two fixture tests, which serve the same files). ✓
- Registry default snapshot + `--hubs db` + `--print-hubs` refresh → Task 4. ✓
- Noise band + `--runs`, per-run spread printed, baseline recorded → Task 4 output + Task 5. ✓
- Keys fail loudly; `--env-file`; worktree/main split → Task 4 guards + Global Constraints. ✓
- ToS: VOTE411 pointer-only never searched, excluded from addressable → `hub_search._is_pointer_only` in `run_race`; unaddressable by construction. ✓
- Secondary precision line (accepted-item quality) → Task 1 `precision`, accumulated + printed pooled in Task 4. ✓
- Manual harness, not pytest; pure scorer unit-tested offline → Task 1; Global Constraints. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases". The Task 5 baseline numbers are produced by running the tool (concrete command), not left blank in code. ✓

**Type consistency:** `score_run` emits `{"id","addressable","retrieved","accepted"}`; `recalls_from_per_source` consumes those keys; `majority_per_source` consumes `{"id","addressable","retrieved_count","accepted_count"}` and emits the `score_run` shape — matches the Task 4 accumulator `acc[...][...] = {"addressable","retrieved_count","accepted_count"}`. `run_race` returns `(hub_domains, found_urls, accepted_urls)`, consumed positionally in `main`. Fixture keys (`race,state,race_label,year,candidates,sources[id,url,accept_urls]`) match `run_race`/`score_run` usage. ✓
