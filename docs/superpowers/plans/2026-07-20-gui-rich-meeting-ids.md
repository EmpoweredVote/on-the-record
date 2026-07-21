# Rich Kind-Aware Meeting IDs + Kind-Aware New-Meeting Form — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the auto-derived meeting ID rich and kind-aware (body/city/chamber, plus guest + race for interviews/forums), and make the new-meeting form show only the fields that apply to the chosen event kind — with a race picker, a guest field, and `modal` as the new default compute.

**Architecture:** FastAPI + Jinja + vanilla JS. A new `gui/races.py` searches `essentials.races` directly via psycopg2 (mirroring `gui/publish_api.py`'s DB-access pattern) and composes a display label + a URL slug per race. `gui/runner.py`'s pure `derive_meeting_id` gains kind-aware locus logic and consumes new `RunParams` fields (`guest`, `race_id`, `race_slug`). The form (`new_meeting.html` + `new_meeting.js`) hides inapplicable fields per a `FIELDS_BY_KIND` map in `gui/formmeta.py`, adds a Guest field and a race typeahead (same shape as the existing politician link-search), and defaults compute to `modal`.

**Tech Stack:** FastAPI, Jinja2, psycopg2, vanilla JS, pytest + `fastapi.testclient.TestClient`.

**This is Plan 2 of 3.** Plan 1 (the workspace shell, on branch `feat/gui-meeting-workspace`) is complete. Plan 3 (richer searchable library) follows. This plan is independently shippable: after it, new meetings get descriptive IDs/URLs and the form is kind-aware. It does NOT change existing meeting slugs (ADR-0002) — derivation applies to NEW meetings only.

## Grounding facts (verified against the live DB, 2026-07-20)

`essentials.races` columns: `id uuid`, `election_id uuid`, `office_id uuid`, `position_name text`, `primary_party text`, `seats int`, `description text`. **No name/slug column** — the human label is `position_name` (e.g. "Governor of Michigan", "U.S. Senate Alabama", "Long Beach Mayor"). `essentials.elections` has `id`, `name`, `election_date date`, `state`. Year comes from `election_date`. ~1747 races (so the picker must search, not list). `run_local.py` already accepts `--race-id <uuid>` (validated + persisted to `pipeline_state.json`); interviews may set `race_id` (per `src/event_entities.py`). The DB is reached via `DATABASE_URL` + psycopg2, exactly as `gui/publish_api.py` does.

---

## Test convention

Run Python via `.venv/bin/python` (never system python3). Full check for this plan:
```bash
.venv/bin/python -m pytest tests/test_gui_runner.py tests/test_gui_races.py tests/test_gui_launch.py -q
```
Fixtures `tmp_meetings_dir` / `tagged_meeting_dir` live in `tests/conftest.py`. The publish tests monkeypatch `gui.publish_api._db_url` — mirror that pattern for `gui.races`.

---

## Task 1: `gui/races.py` — race search, display label, and URL slug

**Files:**
- Create: `gui/races.py`
- Test: `tests/test_gui_races.py`

**Contract:**
- `race_display(position_name, year) -> str` — `"Governor of Michigan · 2026"` (omit `· year` when year is None).
- `race_slug(position_name) -> str` — a clean URL token from the position name: lowercase, non-alnum → `-`, then drop the noise tokens `u`, `s` (from "U.S."), `of`, `the`. `"U.S. Senate Alabama"` → `"senate-alabama"`; `"Governor of Michigan"` → `"governor-michigan"`; `"Long Beach Mayor"` → `"long-beach-mayor"`.
- `search_races_safe(q, *, limit=20) -> {"results": [{"race_id","label","slug"}], "error": None|str}` — best-effort search of `essentials.races` by `position_name`. Returns empty results (no raise) on a <2-char query, no DB configured, or any DB error — mirroring `search_politicians_safe`.

- [ ] **Step 1: Write the failing tests for the pure helpers**

```python
# tests/test_gui_races.py
from __future__ import annotations

from gui.races import race_display, race_slug


def test_race_display_with_and_without_year():
    assert race_display("Governor of Michigan", 2026) == "Governor of Michigan · 2026"
    assert race_display("U.S. Senate Alabama", None) == "U.S. Senate Alabama"


def test_race_slug_strips_us_and_connectives():
    assert race_slug("U.S. Senate Alabama") == "senate-alabama"
    assert race_slug("Governor of Michigan") == "governor-michigan"
    assert race_slug("Governor") == "governor"
    assert race_slug("Long Beach Mayor") == "long-beach-mayor"
    assert race_slug("") == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_races.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gui.races'`.

- [ ] **Step 3: Implement the pure helpers**

```python
# gui/races.py
"""Search essentials.races (via DATABASE_URL + psycopg2, like gui.publish_api) and
compose a human label + a URL slug for a race. Best-effort: when the DB isn't
configured or a query fails, search returns no results rather than raising —
mirroring gui.review_api.search_politicians_safe."""
from __future__ import annotations

import os
import re
from typing import Optional

import psycopg2

# Tokens dropped from a race slug: the "U.S." pair and English connectives.
_SLUG_DROP = {"u", "s", "of", "the"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def race_slug(position_name: str) -> str:
    """A clean URL token from a race's position_name (see module tests)."""
    tokens = [t for t in _slug(position_name).split("-") if t and t not in _SLUG_DROP]
    return "-".join(tokens)


def race_display(position_name: str, year: Optional[int]) -> str:
    """'Governor of Michigan · 2026' (omit the year suffix when year is None)."""
    name = (position_name or "").strip()
    return f"{name} · {year}" if year else name
```

- [ ] **Step 4: Run to verify the helpers pass**

Run: `.venv/bin/python -m pytest tests/test_gui_races.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing tests for `search_races_safe` (no-DB + short-query degradation)**

```python
# tests/test_gui_races.py  (append)
import gui.races as races


def test_search_races_safe_no_db_returns_empty(monkeypatch):
    monkeypatch.setattr(races, "_db_url", lambda: None)
    out = races.search_races_safe("senate")
    assert out == {"results": [], "error": None}


def test_search_races_safe_short_query_returns_empty(monkeypatch):
    monkeypatch.setattr(races, "_db_url", lambda: "postgres://fake")
    out = races.search_races_safe("x")           # <2 chars -> no query attempted
    assert out["results"] == []


def test_search_races_safe_swallows_db_errors(monkeypatch):
    monkeypatch.setattr(races, "_db_url", lambda: "postgres://fake")
    def boom(url):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(races.psycopg2, "connect", boom)
    out = races.search_races_safe("senate")
    assert out["results"] == []
    assert out["error"]                          # a message, not a crash
```

- [ ] **Step 6: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_races.py -k search_races -q`
Expected: FAIL — `search_races_safe` / `_db_url` not defined.

- [ ] **Step 7: Implement `_db_url` + `search_races_safe`**

Append to `gui/races.py`:

```python
def _db_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


def search_races_safe(q: str, *, limit: int = 20) -> dict:
    """Best-effort race search by position_name. Returns
    {"results": [{"race_id","label","slug"}], "error": None|str} — never raises."""
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": [], "error": None}
    url = _db_url()
    if not url:
        return {"results": [], "error": None}
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.id, r.position_name,
                           EXTRACT(YEAR FROM e.election_date)::int AS yr
                    FROM essentials.races r
                    LEFT JOIN essentials.elections e ON e.id = r.election_id
                    WHERE r.position_name ILIKE %s
                    ORDER BY e.election_date DESC NULLS LAST, r.position_name
                    LIMIT %s
                    """,
                    (f"%{query}%", limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:  # DB down / auth / schema — stay best-effort
        return {"results": [], "error": f"race search failed: {exc}"}
    results = [
        {"race_id": str(rid), "label": race_display(name, yr), "slug": race_slug(name)}
        for (rid, name, yr) in rows
    ]
    return {"results": results, "error": None}
```

- [ ] **Step 8: Run to verify all pass**

Run: `.venv/bin/python -m pytest tests/test_gui_races.py -q`
Expected: PASS (all).

- [ ] **Step 9: Commit**

```bash
git add gui/races.py tests/test_gui_races.py
git commit -m "feat(gui): races.py — search essentials.races + label/slug helpers"
```

---

## Task 2: Rich kind-aware `derive_meeting_id` + RunParams/`--race-id`

**Files:**
- Modify: `gui/runner.py`
- Test: `tests/test_gui_runner.py`

Add `guest`, `race_id`, `race_slug` to `RunParams`; rewrite `derive_meeting_id` with kind-aware locus logic; add `--race-id` to `build_run_command`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_runner.py  (append near the existing derive_meeting_id tests)
from gui.runner import RunParams, derive_meeting_id, build_run_command


def _p(**kw):
    base = dict(input="https://x/v", date="2026-05-01", meeting_type="Interview",
                event_kind="news_clip")
    base.update(kw)
    return RunParams(**base)


def test_derive_id_council_prefers_body_then_city():
    p = _p(event_kind="council", meeting_type="Regular Session",
           body_slug="bloomington-city-council", city="Bloomington")
    assert derive_meeting_id(p) == "2026-05-01-bloomington-city-council-regular-session"
    p2 = _p(event_kind="council", meeting_type="Special Session", city="Monroe")
    assert derive_meeting_id(p2) == "2026-05-01-monroe-special-session"


def test_derive_id_floor_uses_label_only():
    p = _p(event_kind="floor", meeting_type="House Floor")
    assert derive_meeting_id(p) == "2026-05-01-house-floor"


def test_derive_id_interview_guest_before_race():
    p = _p(event_kind="news_clip", meeting_type="Interview",
           guest="Xavier Becerra", race_slug="ca-governor")
    assert derive_meeting_id(p) == "2026-05-01-becerra-ca-governor-interview" \
        or derive_meeting_id(p) == "2026-05-01-xavier-becerra-ca-governor-interview"


def test_derive_id_interview_guest_only_then_org():
    p = _p(event_kind="news_clip", meeting_type="Interview", guest="Xavier Becerra")
    assert derive_meeting_id(p) == "2026-05-01-xavier-becerra-interview"
    p2 = _p(event_kind="news_clip", meeting_type="Interview", event_orgs=["CBS"])
    assert derive_meeting_id(p2) == "2026-05-01-cbs-interview"


def test_derive_id_forum_prefers_race():
    p = _p(event_kind="forum", meeting_type="Candidate Forum", race_slug="tx-senate",
           event_orgs=["LWV"])
    assert derive_meeting_id(p) == "2026-05-01-tx-senate-candidate-forum"


def test_derive_id_overlap_dedup():
    # label already contains the locus -> locus dropped, no doubling
    p = _p(event_kind="council", meeting_type="Bloomington Regular Session",
           city="Bloomington")
    mid = derive_meeting_id(p)
    assert mid == "2026-05-01-bloomington-regular-session"


def test_derive_id_length_capped():
    p = _p(event_kind="news_clip", meeting_type="Interview",
           guest="A" * 120)
    assert len(derive_meeting_id(p)) <= 80


def test_build_run_command_includes_race_id():
    p = _p(event_kind="news_clip", race_id="uuid-123")
    cmd = build_run_command("py", "run_local.py", p, "2026-05-01-x-interview")
    assert "--race-id" in cmd and "uuid-123" in cmd
    # absent when no race_id
    p2 = _p(event_kind="news_clip")
    assert "--race-id" not in build_run_command("py", "run_local.py", p2, "m")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_runner.py -k "derive_id or race_id" -q`
Expected: FAIL — `RunParams` has no `guest`/`race_slug`; new derivation not implemented.

- [ ] **Step 3: Add the RunParams fields**

In `gui/runner.py`, add three fields to the `RunParams` dataclass (after `crec_chamber`):

```python
    guest: Optional[str] = None          # interview subject; slugified into the id only
    race_id: Optional[str] = None        # essentials.races UUID -> --race-id
    race_slug: Optional[str] = None       # slug of the race's position_name, for the id
```

- [ ] **Step 4: Rewrite `derive_meeting_id` with kind-aware locus**

Replace the existing `derive_meeting_id` function (keep `_slug` as-is) with:

```python
_MAX_ID_LEN = 80


def _locus_for(p: "RunParams") -> str:
    """The identifying token inserted between date and label, chosen by event kind.
    All inputs are slugged here except body_slug and race_slug (already slugs)."""
    kind = p.event_kind
    org = _slug(p.event_orgs[0]) if p.event_orgs else ""
    city = _slug(p.city or "")
    guest = _slug(p.guest or "")
    race = (p.race_slug or "").strip("-")
    if kind in ("council", "school_board"):
        return (p.body_slug or "").strip() or city
    if kind == "community_meeting":
        return city or org
    if kind in ("debate", "forum"):
        return race or org or city
    if kind in ("news_clip", "press_conference", "podcast"):
        parts = [t for t in (guest, race) if t]      # guest before race
        return "-".join(parts) or org
    if kind == "floor":
        return ""                                     # chamber lives in the label
    return city or org                                # "other"


def derive_meeting_id(p: "RunParams") -> str:
    """Custom id if given, else '{date}-{locus}-{label}' where locus is kind-aware
    (see _locus_for). New meetings only — existing slugs are never re-derived
    (ADR-0002). Raises ValueError if the result isn't a safe path component."""
    if (p.meeting_id or "").strip():
        mid = p.meeting_id.strip()
    else:
        label = _slug(p.meeting_type)
        locus = _locus_for(p)
        # Overlap de-dup: if the label already contains the locus (or vice versa),
        # drop the locus so we don't get 'bloomington-bloomington-...'.
        if locus and (locus in label or label in locus):
            locus = ""
        mid = "-".join(x for x in (p.date, locus, label) if x)
        if len(mid) > _MAX_ID_LEN:
            mid = mid[:_MAX_ID_LEN].rstrip("-")
    mid = mid.strip("-")
    if not is_safe_meeting_id(mid) or mid in ("", "-"):
        raise ValueError(
            f"Cannot derive a valid meeting id from date={p.date!r} type={p.meeting_type!r}"
        )
    return mid
```

- [ ] **Step 5: Add `--race-id` to `build_run_command`**

In `build_run_command`, after the `crec_chamber` block, add:

```python
    if p.race_id:
        cmd += ["--race-id", p.race_id]
```

- [ ] **Step 6: Run to verify all pass**

Run: `.venv/bin/python -m pytest tests/test_gui_runner.py -q`
Expected: PASS (all — including the pre-existing runner tests).

- [ ] **Step 7: Commit**

```bash
git add gui/runner.py tests/test_gui_runner.py
git commit -m "feat(gui): rich kind-aware derive_meeting_id + guest/race in RunParams"
```

---

## Task 3: `FIELDS_BY_KIND` + compute default in `gui/formmeta.py`

**Files:**
- Modify: `gui/formmeta.py`
- Test: `tests/test_gui_launch.py` (the formmeta tests live here)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_launch.py  (append near test_formmeta_covers_all_event_kinds)
def test_fields_by_kind_covers_all_event_kinds():
    from gui.formmeta import FIELDS_BY_KIND
    from src.event_kinds import EVENT_KINDS
    assert set(FIELDS_BY_KIND) == set(EVENT_KINDS)


def test_fields_by_kind_gating():
    from gui.formmeta import FIELDS_BY_KIND
    # council: city + body, no guest/race/crec
    assert "city" in FIELDS_BY_KIND["council"] and "body" in FIELDS_BY_KIND["council"]
    assert "guest" not in FIELDS_BY_KIND["council"]
    # interviews: guest + race, no city/body
    for k in ("news_clip", "press_conference", "podcast"):
        assert "guest" in FIELDS_BY_KIND[k] and "race" in FIELDS_BY_KIND[k]
        assert "city" not in FIELDS_BY_KIND[k] and "body" not in FIELDS_BY_KIND[k]
    # debate/forum: race, no guest
    assert "race" in FIELDS_BY_KIND["forum"] and "guest" not in FIELDS_BY_KIND["forum"]
    # floor: crec only
    assert "crec_chamber" in FIELDS_BY_KIND["floor"]
    assert "city" not in FIELDS_BY_KIND["floor"]


def test_default_compute_is_modal():
    from gui.formmeta import DEFAULT_COMPUTE, DEFAULT_DIARIZER
    assert DEFAULT_COMPUTE == "modal"
    assert DEFAULT_DIARIZER == "oss"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_launch.py -k "fields_by_kind or default_compute" -q`
Expected: FAIL — names not defined.

- [ ] **Step 3: Implement**

Append to `gui/formmeta.py`:

```python
# Which optional fields the new-meeting form shows for each event kind. The
# always-shown fields (source, date, event_kind, title, event_orgs) are NOT
# listed here. Keys must equal EVENT_KINDS (test-enforced). Consumed by the
# template + new_meeting.js to hide inapplicable inputs.
FIELDS_BY_KIND = {
    "council":           ("city", "body"),
    "school_board":      ("city", "body"),
    "debate":            ("race",),
    "forum":             ("race",),
    "community_meeting": ("city",),
    "floor":             ("crec_chamber",),
    "news_clip":         ("guest", "race"),
    "press_conference":  ("guest", "race"),
    "podcast":           ("guest", "race"),
    "other":             ("city",),
}

# GUI form defaults (the CLI keeps its own defaults). Modal is the compute the
# operator reaches for most; oss is the local-quality-default diarizer.
DEFAULT_COMPUTE = "modal"
DEFAULT_DIARIZER = "oss"
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_gui_launch.py -k "fields_by_kind or default_compute or formmeta" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/formmeta.py tests/test_gui_launch.py
git commit -m "feat(gui): FIELDS_BY_KIND gating + modal compute default"
```

---

## Task 4: Race-search route + `/new` accepts guest/race; pass to RunParams

**Files:**
- Modify: `gui/app.py`
- Test: `tests/test_gui_launch.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_launch.py  (append)
def test_races_search_route_returns_json(monkeypatch, tmp_meetings_dir):
    import gui.races as races
    monkeypatch.setattr(races, "search_races_safe",
                        lambda q, **kw: {"results": [
                            {"race_id": "u1", "label": "Governor of Michigan · 2026",
                             "slug": "governor-michigan"}], "error": None})
    from fastapi.testclient import TestClient
    from gui.app import create_app
    resp = TestClient(create_app()).get("/api/races/search", params={"q": "gov"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["slug"] == "governor-michigan"
    assert body["error"] is None


def test_post_new_passes_guest_and_race(monkeypatch, tmp_meetings_dir):
    from gui import runner
    captured = {}
    monkeypatch.setattr(runner, "launch_run",
                        lambda p, **kw: captured.setdefault("p", p) or "2026-05-01-x")
    from fastapi.testclient import TestClient
    from gui.app import create_app
    resp = TestClient(create_app()).post("/new", data={
        "input": "https://x/v", "date": "2026-05-01", "meeting_type": "Interview",
        "event_kind": "news_clip", "guest": "Xavier Becerra",
        "race_id": "uuid-9", "race_slug": "ca-governor",
        "compute": "modal", "diarizer": "oss",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert captured["p"].guest == "Xavier Becerra"
    assert captured["p"].race_id == "uuid-9"
    assert captured["p"].race_slug == "ca-governor"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_launch.py -k "races_search or passes_guest" -q`
Expected: FAIL — route missing; RunParams not populated with the new fields.

- [ ] **Step 3: Add the race-search route**

In `gui/app.py`, add near the existing `politician_search` route:

```python
    @app.get("/api/races/search")
    def race_search(q: str = "") -> JSONResponse:
        from gui import races
        return JSONResponse(races.search_races_safe(q))
```

- [ ] **Step 4: Add the form params + pass them to RunParams**

In `new_meeting_launch`'s signature, add these `Form` params (alongside the existing ones):

```python
        guest: str = Form(""),
        race_id: str = Form(""),
        race_slug: str = Form(""),
```

In the `RunParams(...)` construction inside `new_meeting_launch`, add:

```python
            guest=guest.strip() or None,
            race_id=race_id.strip() or None,
            race_slug=race_slug.strip() or None,
```

Also echo the three new fields in the `dedup_confirm` form-echo dict (so "Process anyway" resubmits them) — add to the `"form": {...}` dict:

```python
                            "guest": guest, "race_id": race_id, "race_slug": race_slug,
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_gui_launch.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add gui/app.py tests/test_gui_launch.py
git commit -m "feat(gui): /api/races/search + /new accepts guest/race_id/race_slug"
```

---

## Task 5: Kind-aware new-meeting form (guest, race picker, modal default, advanced collapsible)

**Files:**
- Modify: `gui/templates/new_meeting.html`, `gui/static/new_meeting.js`
- Modify: `gui/app.py` (`new_meeting_form` passes `FIELDS_BY_KIND` + defaults to the template)
- Test: `tests/test_gui_launch.py`

**Behavior:** Each optional field is wrapped in an element carrying `data-field="<name>"`. `new_meeting.js` reads `FIELDS_BY_KIND` (injected as JSON) and shows only the fields listed for the selected kind; everything else is hidden. Compute defaults to `modal`. The Guest field and a Race typeahead (same markup/behavior as the politician link-search, but hitting `/api/races/search` and writing hidden `race_id`/`race_slug` inputs) are added. Compute/Diarizer/Clip/label move under the existing "Advanced" `<details>`. The floor event label auto-fills "House Floor"/"Senate Floor" tracking the Congressional Record chamber. The derived-ID preview mirrors the new rule.

- [ ] **Step 1: Write the failing route-render + JS-contract tests**

```python
# tests/test_gui_launch.py  (append)
def test_new_form_renders_kind_fields_and_modal_default(tmp_meetings_dir):
    from fastapi.testclient import TestClient
    from gui.app import create_app
    body = TestClient(create_app()).get("/new").text
    # kind-gated fields are present in the markup (JS hides the inapplicable ones)
    assert 'data-field="guest"' in body
    assert 'data-field="race"' in body
    assert 'data-field="body"' in body
    assert 'data-field="crec_chamber"' in body
    # FIELDS_BY_KIND is injected for the client
    assert "FIELDS_BY_KIND" in body
    # compute select defaults to modal (selected option)
    assert 'value="modal" selected' in body or "__DEFAULT_COMPUTE" in body


def test_new_meeting_js_wires_race_search_and_field_gating(tmp_meetings_dir):
    from pathlib import Path
    js = Path("gui/static/new_meeting.js").read_text()
    assert "/api/races/search" in js
    assert "data-field" in js
    assert "race_id" in js and "race_slug" in js
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_launch.py -k "kind_fields or race_search_and_field" -q`
Expected: FAIL — new markup/JS not present.

- [ ] **Step 3: Update `new_meeting_form` to pass the gating data + defaults**

In `gui/app.py`'s `new_meeting_form`, extend the context dict passed to `new_meeting.html`:

```python
        from gui.formmeta import (EVENT_KIND_HELP, COMPUTE_HELP, DIARIZER_HELP,
                                   CITY_REQUIRED_KINDS, MEETING_TYPE_DEFAULTS,
                                   FIELDS_BY_KIND, DEFAULT_COMPUTE, DEFAULT_DIARIZER)
```

and add to the returned context:

```python
                "fields_by_kind": FIELDS_BY_KIND,
                "default_compute": DEFAULT_COMPUTE,
                "default_diarizer": DEFAULT_DIARIZER,
```

- [ ] **Step 4: Rewrite `gui/templates/new_meeting.html`**

Replace the file with the version below. Changes from the current file: every optional field is wrapped in `<div class="fieldwrap" data-field="...">`; a Guest field and a Race typeahead are added; Compute/Diarizer/Clip/label move under Advanced; the Compute `<option>` for `default_compute` is marked selected; `FIELDS_BY_KIND` + defaults are injected as JSON.

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>New meeting</title><link rel="stylesheet" href="/static/style.css"></head>
<body>
  <header><a class="back" href="/">← Library</a><h1>Process a new meeting</h1></header>
  <main class="review newpage" data-city-required="{{ city_required_kinds|join(',') }}">
    <form method="post" action="/new" class="newform" id="newform">
      <label>Source URL or file path
        <input type="text" name="input" id="f-input" required placeholder="https://… or /path/to/video.mp4">
        <small class="help" id="source-meta-note" aria-live="polite"></small>
      </label>
      <label>Date (YYYY-MM-DD)
        <input type="text" name="date" id="f-date" placeholder="2026-02-10" required>
      </label>
      <label>Event kind
        <select name="event_kind" id="f-kind">
          {% for k in event_kinds %}<option value="{{ k }}">{{ k }}</option>{% endfor %}
        </select>
        <small class="help" id="kind-help"></small>
      </label>

      <div class="fieldwrap" data-field="city">
        <label id="city-label">City <span class="req" id="city-req" hidden>(required)</span>
          <input type="text" name="city" id="f-city" placeholder="Bloomington">
        </label>
      </div>

      <div class="fieldwrap" data-field="body">
        <label>Body / roster (for council &amp; school-board meetings)
          <select name="body_slug" id="f-body">
            <option value="">— none —</option>
            {% for slug, label in cached_rosters %}<option value="{{ slug }}">{{ label }}</option>{% endfor %}
          </select>
          <small class="help">Guides speaker naming and links the meeting to its Chamber. Only cached rosters appear (refresh with refresh_roster.py).</small>
        </label>
      </div>

      <div class="fieldwrap" data-field="guest">
        <label>Guest / subject — who is being interviewed
          <input type="text" name="guest" id="f-guest" placeholder="e.g. Xavier Becerra" autocomplete="off">
          <small class="help">Optional. Folded into the meeting id/URL.</small>
        </label>
      </div>

      <div class="fieldwrap" data-field="race">
        <label>Race — links the meeting to an essentials race
          <div class="race-search" id="f-race" data-search-url="/api/races/search">
            <input type="text" id="f-race-input" placeholder="Search a race… (e.g. Governor, Senate)" autocomplete="off">
            <div class="race-results" id="f-race-results"></div>
            <div class="race-chosen" id="f-race-chosen" hidden></div>
          </div>
          <input type="hidden" name="race_id" id="f-race-id">
          <input type="hidden" name="race_slug" id="f-race-slug">
          <small class="help">Optional. Sets the race for publishing and adds it to the id/URL.</small>
        </label>
      </div>

      <div class="fieldwrap" data-field="crec_chamber">
        <label>Congressional Record (U.S. House/Senate floor: identify speakers from the Record for the meeting date)
          <select name="crec_chamber" id="f-crec-chamber">
            <option value="">— none —</option>
            <option value="house">House</option>
            <option value="senate">Senate</option>
          </select>
        </label>
      </div>

      <label>Title — the headline shown on the site
        <input type="text" name="title" id="f-title" placeholder="e.g. Xavier Becerra — The Race for Governor">
        <small class="help">Optional. If blank, the site uses the video's own title, then falls back to "city + label".</small>
      </label>
      <label>Event org(s) — "Produced by …" on the site (comma-separated)
        <input type="text" name="event_orgs" id="f-orgs" placeholder="e.g. CBS, NBC">
      </label>

      <details class="advanced"><summary>Processing &amp; advanced</summary>
        <label>Compute
          <select name="compute" id="f-compute">
            {% for c, h in compute_help.items() %}<option value="{{ c }}"{% if c == default_compute %} selected{% endif %}>{{ c }}</option>{% endfor %}
          </select>
          <small class="help" id="compute-help"></small>
        </label>
        <label>Diarizer
          <select name="diarizer" id="f-diarizer">
            {% for d, h in diarizer_help.items() %}<option value="{{ d }}"{% if d == default_diarizer %} selected{% endif %}>{{ d }}</option>{% endfor %}
          </select>
          <small class="help" id="diarizer-help"></small>
        </label>
        <label>Clip (optional — process only part of the source)
          <span class="cliprow">
            <input type="text" name="clip_start" id="f-clipstart" placeholder="start 10:00">
            <input type="text" name="clip_end" id="f-clipend" placeholder="end 20:00">
          </span>
        </label>
        <label>Event label / URL slug (auto-filled from the kind — edit only if you need a specific label)
          <input type="text" name="meeting_type" id="f-mtype" placeholder="Regular Session">
        </label>
        <p class="mid">Meeting ID (auto-derived): <span id="derived-id">—</span></p>
      </details>
      <button type="submit" class="enroll">Start processing</button>
    </form>

    <aside class="previewcard">
      <div class="previewlabel">How this will look on ontherecord.com</div>
      <div id="preview" class="preview">
        <div class="pv-title" id="pv-title">—</div>
        <div class="pv-meta"><span id="pv-kind" class="pv-badge"></span> <span id="pv-sub"></span></div>
      </div>
    </aside>
  </main>

  <script>
    window.__EVENT_KIND_HELP = {{ event_kind_help|tojson }};
    window.__COMPUTE_HELP = {{ compute_help|tojson }};
    window.__DIARIZER_HELP = {{ diarizer_help|tojson }};
    window.__MEETING_TYPE_DEFAULTS = {{ meeting_type_defaults|tojson }};
    window.__FIELDS_BY_KIND = {{ fields_by_kind|tojson }};
  </script>
  <script src="/static/new_meeting.js"></script>
</body></html>
```

- [ ] **Step 5: Rewrite `gui/static/new_meeting.js`**

Replace the file with the version below. It keeps the existing preview / derived-id / source-meta logic, and adds: `applyKindFields()` (show/hide `.fieldwrap` by `__FIELDS_BY_KIND`), the floor-label-tracks-chamber default, a race typeahead, and guest/race in the derived-id preview.

```javascript
// Live new-meeting form: kind-aware field gating, preview card, auto-derived
// meeting id, source-metadata autofill, and a race typeahead. Display-only —
// the server is authoritative for derivation and validation.
(function () {
  const main = document.querySelector("main.newpage");
  if (!main) return;
  const cityRequired = (main.getAttribute("data-city-required") || "").split(",").filter(Boolean);
  const FIELDS = window.__FIELDS_BY_KIND || {};

  const $ = (id) => document.getElementById(id);
  const input = { kind: $("f-kind"), city: $("f-city"), mtype: $("f-mtype"),
                  date: $("f-date"), title: $("f-title"), guest: $("f-guest"),
                  crec: $("f-crec-chamber") };

  const DEFAULTS = window.__MEETING_TYPE_DEFAULTS || {};
  const DEFAULT_VALUES = new Set(Object.values(DEFAULTS).filter(Boolean));
  // Floor labels tracking the Congressional Record chamber (added to the
  // auto-applied set so they never clobber a hand-typed label).
  ["House Floor", "Senate Floor"].forEach((v) => DEFAULT_VALUES.add(v));

  const slug = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  const raceSlug = (s) => slug(s).split("-").filter((t) => t && !["u","s","of","the"].includes(t)).join("-");

  function applyKindDefault() {
    const cur = input.mtype.value.trim();
    if (cur === "" || DEFAULT_VALUES.has(cur)) {
      // Floor: track the chamber selection; otherwise the per-kind default.
      let def = DEFAULTS[input.kind.value] || "";
      if (input.kind.value === "floor" && input.crec && input.crec.value === "senate") {
        def = "Senate Floor";
      }
      input.mtype.value = def;
    }
  }

  function applyKindFields() {
    const shown = new Set(FIELDS[input.kind.value] || []);
    main.querySelectorAll(".fieldwrap").forEach((el) => {
      el.hidden = !shown.has(el.getAttribute("data-field"));
    });
  }

  function currentLocus() {
    const kind = input.kind.value;
    const org = slug((($("f-orgs").value || "").split(",")[0] || "").trim());
    const city = slug(input.city.value.trim());
    const guest = slug((input.guest && input.guest.value || "").trim());
    const race = ($("f-race-slug").value || "").trim();
    const body = ($("f-body") && $("f-body").value || "").trim();
    if (kind === "council" || kind === "school_board") return body || city;
    if (kind === "community_meeting") return city || org;
    if (kind === "debate" || kind === "forum") return race || org || city;
    if (kind === "news_clip" || kind === "press_conference" || kind === "podcast")
      return [guest, race].filter(Boolean).join("-") || org;
    if (kind === "floor") return "";
    return city || org;
  }

  function refresh() {
    const kind = input.kind.value;
    const mtype = input.mtype.value.trim();
    const date = input.date.value.trim();
    const title = input.title.value.trim();
    const city = input.city.value.trim();

    $("kind-help").textContent = (window.__EVENT_KIND_HELP || {})[kind] || "";
    $("compute-help").textContent = (window.__COMPUTE_HELP || {})[$("f-compute").value] || "";
    $("diarizer-help").textContent = (window.__DIARIZER_HELP || {})[$("f-diarizer").value] || "";

    // derived id: {date}-{locus}-{label}, with the label-contains-locus de-dup
    const label = slug(mtype);
    let locus = currentLocus();
    if (locus && (label.includes(locus) || locus.includes(label))) locus = "";
    const parts = [date, locus, label].filter(Boolean);
    $("derived-id").textContent = (date && label) ? parts.join("-") : "—";

    $("pv-title").textContent = title || [city, mtype].filter(Boolean).join(" ") || "(untitled)";
    $("pv-kind").textContent = kind;
    $("pv-sub").textContent = [date].filter(Boolean).join(" · ");

    const needCity = cityRequired.includes(kind);
    $("city-req").hidden = !needCity;
    input.city.toggleAttribute("required", needCity);
  }

  // --- Race typeahead (mirrors the review link-search) ---
  const raceWidget = $("f-race");
  const raceInput = $("f-race-input");
  const raceResults = $("f-race-results");
  const raceChosen = $("f-race-chosen");
  let raceTimer = null;
  function chooseRace(id, sslug, labelText) {
    $("f-race-id").value = id;
    $("f-race-slug").value = sslug || raceSlug(labelText);
    raceChosen.hidden = false;
    raceChosen.textContent = "✓ " + labelText + " (clear)";
    raceResults.innerHTML = "";
    raceInput.value = "";
    refresh();
  }
  if (raceChosen) raceChosen.addEventListener("click", () => {
    $("f-race-id").value = ""; $("f-race-slug").value = "";
    raceChosen.hidden = true; raceChosen.textContent = ""; refresh();
  });
  if (raceInput) raceInput.addEventListener("input", () => {
    const q = raceInput.value.trim();
    clearTimeout(raceTimer);
    if (q.length < 2) { raceResults.innerHTML = ""; return; }
    raceTimer = setTimeout(async () => {
      const url = (raceWidget.getAttribute("data-search-url") || "/api/races/search") + "?q=" + encodeURIComponent(q);
      let data;
      try { data = await (await fetch(url)).json(); }
      catch (_) { raceResults.innerHTML = '<div class="link-msg">search unavailable</div>'; return; }
      const list = data.results || [];
      if (data.error || !list.length) {
        raceResults.innerHTML = '<div class="link-msg">' + (data.error ? "search unavailable" : "no matches") + "</div>";
        return;
      }
      raceResults.innerHTML = "";
      list.forEach((r) => {
        const b = document.createElement("button");
        b.type = "button"; b.className = "link-result"; b.textContent = r.label;
        b.addEventListener("click", () => chooseRace(r.race_id, r.slug, r.label));
        raceResults.appendChild(b);
      });
    }, 250);
  });

  // --- Source metadata autofill (unchanged from the prior version) ---
  const sourceInput = $("f-input");
  const note = $("source-meta-note");
  let lastFetched = null;
  const looksLikeUrl = (s) => /^https?:\/\//i.test(s.trim());
  const fillIfEmpty = (el, value) => { if (el && value && el.value.trim() === "") el.value = value; };
  async function fetchSourceMeta() {
    const url = sourceInput.value.trim();
    if (!looksLikeUrl(url) || url === lastFetched) return;
    lastFetched = url;
    note.textContent = "Fetching video details…";
    try {
      const resp = await fetch("/api/source-meta?url=" + encodeURIComponent(url));
      if (!resp.ok) throw new Error("bad status");
      const data = await resp.json();
      if (!data.date && !data.title && !data.event_org) { note.textContent = ""; return; }
      fillIfEmpty(input.date, data.date);
      fillIfEmpty(input.title, data.title);
      fillIfEmpty($("f-orgs"), data.event_org);
      note.textContent = "";
      refresh();
    } catch (e) { note.textContent = "Couldn't fetch details — fill in manually."; }
  }
  sourceInput.addEventListener("blur", fetchSourceMeta);
  sourceInput.addEventListener("change", fetchSourceMeta);
  sourceInput.addEventListener("paste", () => setTimeout(fetchSourceMeta, 0));

  main.querySelectorAll("input, select").forEach((el) => {
    el.addEventListener("input", refresh);
    el.addEventListener("change", refresh);
  });
  input.kind.addEventListener("change", () => { applyKindDefault(); applyKindFields(); refresh(); });
  if (input.crec) input.crec.addEventListener("change", () => { applyKindDefault(); refresh(); });
  applyKindDefault();
  applyKindFields();
  refresh();
})();
```

- [ ] **Step 6: Add CSS for the race widget**

Append to `gui/static/style.css`:

```css
.fieldwrap[hidden] { display: none; }
.race-search { position: relative; }
.race-search input { padding: 0.3rem 0.4rem; border: 1px solid #ccc; border-radius: 0.4rem; font-size: 0.95rem; width: 100%; }
.race-results { display: flex; flex-direction: column; gap: 0.2rem; margin-top: 0.3rem; }
.race-chosen { margin-top: 0.3rem; font-size: 0.85rem; color: #1b7a3d; cursor: pointer; }
```

- [ ] **Step 7: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_gui_launch.py -q`
Expected: PASS (all).

- [ ] **Step 8: Manual smoke check** (JS behavior the tests can't cover)

Start the GUI (`.venv/bin/python -m gui`), open `/new`. Verify: switching Event kind shows/hides the right fields (council → City + Body; news_clip → Guest + Race; floor → Congressional Record; interview kinds hide City/Body); Compute defaults to `modal`; typing in the Race box lists matches and choosing one updates the "Meeting ID (auto-derived)" preview; selecting Senate under a floor kind sets the label to "Senate Floor".

- [ ] **Step 9: Commit**

```bash
git add gui/templates/new_meeting.html gui/static/new_meeting.js gui/static/style.css gui/app.py tests/test_gui_launch.py
git commit -m "feat(gui): kind-aware new-meeting form with guest + race picker, modal default"
```

---

## Self-Review (completed during planning)

- **Spec coverage (Section 3 — rich IDs):** kind-aware locus (Task 2 `_locus_for`), guest+race for interviews (Tasks 1, 2, 4, 5), race slug from `position_name` (Task 1), overlap de-dup + length cap (Task 2), collision suffix unchanged (existing `_unique_meeting_id`, untouched), race picker sets `race_id` → `--race-id` (Tasks 1, 4, 5 + Task 2 `build_run_command`). New-meetings-only / ADR-0002 preserved (derivation only runs for new meetings; existing dirs use `--resume`).
- **Spec coverage (Section 4 — kind-aware form):** field gating via `FIELDS_BY_KIND` (Task 3) applied in template + JS (Task 5), guest field + race picker (Task 5), `modal` default (Tasks 3, 5), Processing & advanced collapsible (Task 5), floor label tracks chamber (Task 5), city-required guard unchanged (existing server check retained).
- **Type consistency:** `RunParams.guest/race_id/race_slug` (Task 2) are read by `_locus_for`/`build_run_command` (Task 2) and set by `new_meeting_launch` (Task 4). `search_races_safe` returns `{results:[{race_id,label,slug}],error}` (Task 1), consumed by the route (Task 4) and the JS typeahead (Task 5) with matching keys. `FIELDS_BY_KIND` (Task 3) is injected as `__FIELDS_BY_KIND` and read by `applyKindFields` (Task 5).
- **Placeholder scan:** none — every step has complete code.
- **Deferred to Plan 3 / out of scope:** the library's per-row race label reuses `gui/races.py` helpers (Plan 3 wires it). Interview `race_id` → published `event_races` behavior is existing pipeline logic (unchanged here). The exact "guest before race" ordering is asserted by tests; the guest slug uses the full name (`xavier-becerra`), acceptable and length-capped.
```
