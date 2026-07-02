# Processing GUI — Slice 2c: Politician Linking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

Your original pain #3: "select the politician it's connected to." Adds, on each review-page speaker card, a **live politician search** (essentials `search_politicians`) → click a result to **link** the speaker to that politician (`politician_slug` + `politician_id`), plus an **Unlink**. Persists through the Slice 2b `persist_review` path. Builds on 2a/2b.

**Deferred (later slices):** 2d merge / unidentified / not-a-speaker; 2e enrollment; create-local-person (non-essentials people) — 2c is essentials linking only.

**Goal:** Search essentials from a speaker card and link/unlink the matched politician, persisted to `transcript_named.json`.

**Architecture:** A JSON search endpoint (`GET /api/politicians/search?q=`) wraps `src.essentials_client.search_politicians` best-effort (network failure / short query → `{results: [], error}`, never 500). `review_api.apply_link`/`apply_unlink` mutate via `review.link_speaker` then `persist_review` (reused from 2b). `SpeakerCard` carries `politician_slug`/`politician_id` for display. `review.js` gains a debounced search widget that renders each result as a native POST form to the link route (Post/Redirect/Get, consistent with 2b).

**Tech Stack:** FastAPI `Form`/`JSONResponse`; `src.essentials_client.search_politicians` (+ `EssentialsClientError`), `src.review.link_speaker`. Tests monkeypatch `src.essentials_client.search_politicians` (no network).

---

### Task 1: `SpeakerCard` link fields + load + display

**Files:**
- Modify: `gui/models.py`
- Modify: `gui/review_api.py`
- Modify: `gui/templates/review.html`
- Modify: `gui/static/style.css`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
def test_speaker_card_carries_politician_link_fields():
    from gui.models import SpeakerCard
    c = SpeakerCard(label="S", name="Tom Steyer", confidence=1.0, method="human_review",
                    minutes=2, seg_count=3, politician_slug="tom-steyer", politician_id="uuid-1")
    assert c.politician_slug == "tom-steyer"
    assert c.politician_id == "uuid-1"
    assert c.is_linked is True
    assert SpeakerCard(label="S", name=None, confidence=0, method=None,
                       minutes=0, seg_count=0).is_linked is False


def test_load_review_page_populates_link_fields(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    # link SPEAKER_00 in the on-disk meeting
    import json as _json
    data = _json.loads((mdir / "transcript_named.json").read_text())
    data["speakers"]["SPEAKER_00"]["politician_slug"] = "mayor-johnson"
    data["speakers"]["SPEAKER_00"]["politician_id"] = "uuid-mj"
    (mdir / "transcript_named.json").write_text(_json.dumps(data))

    page = load_review_page("2026-02-04-council")
    card = [c for c in page.confirmed if c.label == "SPEAKER_00"][0]
    assert card.politician_slug == "mayor-johnson"
    assert card.is_linked is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "link_fields or populates_link" -v`
Expected: FAIL — `SpeakerCard` has no `politician_slug`.

- [ ] **Step 3: Add fields to `SpeakerCard` in `gui/models.py`**

Add two fields (after `clip_seeks`) and an `is_linked` property:

```python
    politician_slug: Optional[str] = None
    politician_id: Optional[str] = None
```

```python
    @property
    def is_linked(self) -> bool:
        return bool(self.politician_slug)
```

- [ ] **Step 4: Populate them in `gui/review_api.py` `load_review_page`**

Where each `SpeakerCard(...)` is built inside the loop, pull the mapping and pass the link fields:

```python
        mapping = meeting.speakers.get(v.label)
        card = SpeakerCard(
            label=v.label,
            name=v.current_name,
            confidence=v.current_confidence,
            method=v.current_method,
            minutes=v.total_speech_seconds / 60.0,
            seg_count=v.seg_count,
            sample_text=v.sample_text,
            hints=[(h[0], h[1]) for h in v.soft_hints[:3]],
            clip_seeks=[_seek(c, is_video=is_video, clip_offset=clip_offset)
                        for c in v.clip_candidates],
            politician_slug=getattr(mapping, "politician_slug", None) if mapping else None,
            politician_id=getattr(mapping, "politician_id", None) if mapping else None,
        )
```

- [ ] **Step 5: Show the link in `gui/templates/review.html`**

Inside the `card` macro, just above the `.actions` block, add:

```html
      {% if c.is_linked %}
      <div class="linked">🔗 linked: <span class="pslug">{{ c.politician_slug }}</span></div>
      {% endif %}
```

Append to `gui/static/style.css`:

```css
.linked { font-size: 0.85rem; color: #1b7a3d; margin-top: 0.4rem; }
.linked .pslug { font-family: ui-monospace, monospace; }
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "link_fields or populates_link" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/models.py gui/review_api.py gui/templates/review.html gui/static/style.css tests/test_gui_review.py
git commit -m "feat(gui): SpeakerCard carries + displays politician link

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `search_politicians_safe` + `apply_link` / `apply_unlink`

**Files:**
- Modify: `gui/review_api.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
from gui.review_api import apply_link, apply_unlink, search_politicians_safe


def test_search_politicians_safe_success(monkeypatch):
    import src.essentials_client as ec
    monkeypatch.setattr(ec, "search_politicians", lambda q, **kw: [
        {"politician_slug": "tom-steyer", "politician_id": "u1", "full_name": "Tom Steyer",
         "office_title": "Governor", "district_label": "", "government_name": "California",
         "is_incumbent": False},
    ])
    out = search_politicians_safe("steyer")
    assert out["error"] is None
    assert out["results"][0]["politician_slug"] == "tom-steyer"
    assert out["results"][0]["full_name"] == "Tom Steyer"


def test_search_politicians_safe_swallows_errors(monkeypatch):
    import src.essentials_client as ec
    def boom(q, **kw):
        raise ec.EssentialsClientError("nope", code="INVALID_QUERY", status=None)
    monkeypatch.setattr(ec, "search_politicians", boom)
    out = search_politicians_safe("x")
    assert out["results"] == []
    assert out["error"]  # a message, not a crash


def test_apply_link_and_unlink(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)

    assert apply_link("2026-02-04-council", "SPEAKER_01", "clerk-smith", "uuid-cs") is True
    page = load_review_page("2026-02-04-council")
    card = [c for c in (page.confirmed + page.needs_attention) if c.label == "SPEAKER_01"][0]
    assert card.politician_slug == "clerk-smith"

    assert apply_unlink("2026-02-04-council", "SPEAKER_01") is True
    page2 = load_review_page("2026-02-04-council")
    card2 = [c for c in (page2.confirmed + page2.needs_attention) if c.label == "SPEAKER_01"][0]
    assert card2.is_linked is False


def test_apply_link_guards(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_link("ghost", "SPEAKER_00", "s", "i") is False           # unknown meeting
    assert apply_link("2026-02-04-council", "SPEAKER_99", "s", "i") is False  # unknown label
    assert apply_link("2026-02-04-council", "SPEAKER_00", "", "i") is False   # empty slug
    assert apply_link("../x", "SPEAKER_00", "s", "i") is False            # unsafe id
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "search_politicians_safe or apply_link or apply_unlink" -v`
Expected: FAIL — names not importable.

- [ ] **Step 3: Implement in `gui/review_api.py`**

```python
def search_politicians_safe(q: str, *, limit: int = 10) -> dict:
    """Best-effort essentials name search. Returns {"results": [...], "error": None|str}
    — never raises, so a network/HTTP/short-query failure just yields no results."""
    from src.essentials_client import EssentialsClientError, search_politicians
    try:
        raw = search_politicians(q, limit=limit)
    except EssentialsClientError as exc:
        return {"results": [], "error": str(exc)}
    except Exception as exc:  # transport/unexpected — stay best-effort
        return {"results": [], "error": f"search failed: {exc}"}
    results = [
        {
            "politician_slug": r.get("politician_slug"),
            "politician_id": r.get("politician_id"),
            "full_name": r.get("full_name"),
            "office_title": r.get("office_title"),
            "district_label": r.get("district_label"),
            "government_name": r.get("government_name"),
        }
        for r in raw
    ]
    return {"results": results, "error": None}


def apply_link(meeting_id: str, label: str, politician_slug: str, politician_id: str) -> bool:
    """Link a speaker to an essentials politician and persist. False on unsafe/unknown/empty slug."""
    slug = (politician_slug or "").strip()
    if not slug:
        return False
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, _roster = ctx
    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False
    from src import review
    review.link_speaker(meeting.speakers, label, slug, (politician_id or "").strip() or None)
    persist_review(meeting, meeting_dir)
    return True


def apply_unlink(meeting_id: str, label: str) -> bool:
    """Clear a speaker's politician link and persist. False on unsafe/unknown."""
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, _roster = ctx
    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False
    from src import review
    review.link_speaker(meeting.speakers, label, None, None)
    persist_review(meeting, meeting_dir)
    return True
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "search_politicians_safe or apply_link or apply_unlink" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/review_api.py tests/test_gui_review.py
git commit -m "feat(gui): search_politicians_safe + apply_link/apply_unlink

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: search JSON route + link/unlink POST routes

**Files:**
- Modify: `gui/app.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
def test_search_route_returns_json(monkeypatch, tmp_meetings_dir):
    import src.essentials_client as ec
    monkeypatch.setattr(ec, "search_politicians", lambda q, **kw: [
        {"politician_slug": "tom-steyer", "politician_id": "u1", "full_name": "Tom Steyer",
         "office_title": "Governor", "district_label": "", "government_name": "CA"},
    ])
    client = TestClient(create_app())
    resp = client.get("/api/politicians/search", params={"q": "steyer"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["politician_slug"] == "tom-steyer"
    assert body["error"] is None


def test_search_route_error_is_200_empty(monkeypatch, tmp_meetings_dir):
    import src.essentials_client as ec
    monkeypatch.setattr(ec, "search_politicians",
                        lambda q, **kw: (_ for _ in ()).throw(ec.EssentialsClientError("bad", code="X", status=None)))
    client = TestClient(create_app())
    resp = client.get("/api/politicians/search", params={"q": "z"})
    assert resp.status_code == 200          # best-effort: not a 500
    assert resp.json()["results"] == []


def test_link_and_unlink_routes(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app())

    r = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/link",
                    data={"politician_slug": "clerk-smith", "politician_id": "uuid-cs"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "clerk-smith" in client.get("/meetings/2026-02-04-council/review").text

    r2 = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/unlink", follow_redirects=False)
    assert r2.status_code == 303

    assert client.post("/meetings/ghost/speakers/SPEAKER_00/link",
                       data={"politician_slug": "s"}, follow_redirects=False).status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "search_route or link_and_unlink" -v`
Expected: FAIL — routes don't exist.

- [ ] **Step 3: Add routes to `gui/app.py`**

Add import:

```python
from fastapi.responses import JSONResponse
```

Inside `create_app()`, after the rename route:

```python
    @app.get("/api/politicians/search")
    def politician_search(q: str = "") -> JSONResponse:
        return JSONResponse(review_api.search_politicians_safe(q))

    @app.post("/meetings/{meeting_id}/speakers/{label}/link")
    def link_speaker_route(meeting_id: str, label: str,
                           politician_slug: str = Form(""), politician_id: str = Form("")):
        redirect = RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)
        if not politician_slug.strip():
            return redirect
        if not review_api.apply_link(meeting_id, label, politician_slug, politician_id):
            raise HTTPException(status_code=404)
        return redirect

    @app.post("/meetings/{meeting_id}/speakers/{label}/unlink")
    def unlink_speaker_route(meeting_id: str, label: str):
        if not review_api.apply_unlink(meeting_id, label):
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "search_route or link_and_unlink" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/app.py tests/test_gui_review.py
git commit -m "feat(gui): politician search JSON route + link/unlink POST routes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Link UI — unlink button + search widget (JS)

**Files:**
- Modify: `gui/templates/review.html`
- Modify: `gui/static/review.js`
- Modify: `gui/static/style.css`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_review.py`:

```python
def test_review_page_has_link_widget_and_unlink(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    import json as _json
    data = _json.loads((mdir / "transcript_named.json").read_text())
    data["speakers"]["SPEAKER_00"]["politician_slug"] = "mayor-johnson"
    (mdir / "transcript_named.json").write_text(_json.dumps(data))

    body = TestClient(create_app()).get("/meetings/2026-02-04-council/review").text
    # search widget present for a speaker, wired to the search API in JS
    assert 'link-search' in body
    assert '/api/politicians/search' in body  # referenced from review.js (served inline check below)
    # unlink form for the already-linked SPEAKER_00
    assert 'action="/meetings/2026-02-04-council/speakers/SPEAKER_00/unlink"' in body


def test_review_js_references_search_and_link(tmp_meetings_dir):
    from pathlib import Path
    js = Path("gui/static/review.js").read_text()
    assert "/api/politicians/search" in js
    assert "/link" in js
```

(The first test asserts `/api/politicians/search` appears in the served review page; put a `data-search-url` attribute on the widget so it shows up in the HTML, and also reference it in `review.js` for the second test.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "link_widget or js_references" -v`
Expected: FAIL — widget/JS not present.

- [ ] **Step 3: Add the widget + unlink to `gui/templates/review.html`**

Replace the `.actions` block's contents to append link controls (keep the existing accept + rename forms, add after them, before closing `</div>`):

```html
        {% if c.is_linked %}
        <form method="post" action="/meetings/{{ page.meeting_id }}/speakers/{{ c.label }}/unlink">
          <button type="submit" class="unlink">Unlink</button>
        </form>
        {% endif %}
        <div class="link-search"
             data-search-url="/api/politicians/search"
             data-link-action="/meetings/{{ page.meeting_id }}/speakers/{{ c.label }}/link">
          <input type="text" placeholder="Link politician… (type a name)" autocomplete="off">
          <div class="link-results"></div>
        </div>
```

- [ ] **Step 4: Add the search widget to `gui/static/review.js`**

Append (keep the existing clip handler):

```javascript
// Politician link search: debounced query to the search API; each result is a
// native POST form to the link route (Post/Redirect/Get, like rename).
(function () {
  const DEBOUNCE = 250;
  document.addEventListener("input", function (e) {
    const input = e.target;
    if (!input.matches(".link-search input")) return;
    const widget = input.closest(".link-search");
    const results = widget.querySelector(".link-results");
    const q = input.value.trim();
    clearTimeout(widget._t);
    if (q.length < 2) { results.innerHTML = ""; return; }
    widget._t = setTimeout(async () => {
      const url = widget.getAttribute("data-search-url") + "?q=" + encodeURIComponent(q);
      let data;
      try {
        const resp = await fetch(url);
        data = await resp.json();
      } catch (_) {
        results.innerHTML = '<div class="link-msg">search unavailable</div>';
        return;
      }
      if (data.error || !data.results.length) {
        results.innerHTML = '<div class="link-msg">' + (data.error ? "search unavailable" : "no matches") + "</div>";
        return;
      }
      const action = widget.getAttribute("data-link-action");
      results.innerHTML = data.results.map((r) => {
        const label = [r.full_name, r.office_title, r.government_name].filter(Boolean).join(" · ");
        const esc = (s) => String(s == null ? "" : s).replace(/"/g, "&quot;").replace(/</g, "&lt;");
        return (
          '<form method="post" action="' + action + '">' +
          '<input type="hidden" name="politician_slug" value="' + esc(r.politician_slug) + '">' +
          '<input type="hidden" name="politician_id" value="' + esc(r.politician_id) + '">' +
          '<button type="submit" class="link-result">' + esc(label) + "</button>" +
          "</form>"
        );
      }).join("");
    }, DEBOUNCE);
  });
})();
```

- [ ] **Step 5: Append styles to `gui/static/style.css`**

```css
.link-search { position: relative; }
.link-search input { padding: 0.2rem 0.4rem; border: 1px solid #ccc; border-radius: 0.4rem; font-size: 0.85rem; min-width: 16rem; }
.link-results { display: flex; flex-direction: column; gap: 0.2rem; margin-top: 0.3rem; }
.link-results form { margin: 0; }
button.link-result { text-align: left; width: 100%; padding: 0.25rem 0.5rem; border: 1px solid #d0d7e2; border-radius: 0.4rem; background: #f4f7fc; cursor: pointer; font-size: 0.85rem; }
button.link-result:hover { background: #e6edfa; }
button.unlink { padding: 0.2rem 0.6rem; border: 1px solid #d0a0a0; border-radius: 0.4rem; background: #fdeaea; color: #b32020; cursor: pointer; font-size: 0.85rem; }
.link-msg { font-size: 0.8rem; color: #999; }
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "link_widget or js_references" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/templates/review.html gui/static/review.js gui/static/style.css tests/test_gui_review.py
git commit -m "feat(gui): politician search widget + unlink on review cards

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full-suite regression + manual smoke

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (Slices 1–2c), no regressions.

- [ ] **Step 2: Manual smoke**

Run: `.venv/bin/python -m gui`; open a meeting's review page → in a speaker's "Link politician…" box, type a name → matching politicians appear → click one → page reloads showing "🔗 linked: <slug>" and an Unlink button. (Requires essentials reachable; if the search box shows "search unavailable", that's the best-effort path with no network — not a bug.) Ctrl-C to stop (no listener left on 8000).

---

## Self-Review

**Spec coverage:** Live search from a card (Task 3 JSON route + Task 4 widget) ✅ · link to a matched politician (Task 2 `apply_link` via `review.link_speaker` + Task 3 POST + Task 4 result forms) ✅ · unlink (Task 2 `apply_unlink` + Task 3 route + Task 4 button) ✅ · link displayed on cards (Task 1) ✅ · persistence via 2b `persist_review` ✅ · best-effort search never 500s (Task 2 `search_politicians_safe`) ✅ · guards unsafe/unknown/empty → 404/no-op (Task 2/3) ✅ · scope: essentials linking only, no merge/unidentified/enroll/local-person ✅.

**Placeholder scan:** none — complete code + exact commands.

**Type consistency:** `apply_link(meeting_id, label, slug, id) -> bool`, `apply_unlink(meeting_id, label) -> bool`, `search_politicians_safe(q, *, limit) -> {"results","error"}` consistent across `review_api`, routes, tests. New `SpeakerCard.politician_slug/politician_id/is_linked` used identically in `review_api`, template, JS data attributes, tests. Reuses `_load_meeting_ctx`/`persist_review`/`is_safe_meeting_id` from 2a/2b unchanged. Routes redirect to `/meetings/{id}/review` (matches 2a GET). JS result forms POST the exact field names (`politician_slug`, `politician_id`) the link route reads via `Form`.
