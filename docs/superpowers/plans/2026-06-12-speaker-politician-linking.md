# Speaker → Politician/Candidate Linking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the reviewer link a speaker to any essentials politician/candidate once during CLI review, and have that link propagate automatically to every future meeting via voice matching — with no schema or downstream changes.

**Architecture:** Pipeline-side only. Add an essentials name-search client call; add a pure linking core + an interactive link prompt to the review loop; connect two existing-but-disconnected wires so (1) voice matches carry a profile's politician identity and (2) enrollment keys any identity-bearing speaker under `essentials:<slug>`, absorbing a pre-existing local-slug profile. `publish.py` already writes the identity columns, and `reenroll_profiles.py` already rebuilds profiles from transcripts, so correction and propagation come for free.

**Tech Stack:** Python 3, `requests`, `numpy`, `pytest`. No new dependencies.

**Conventions that MUST be followed:**
- `SpeakerMapping` / `StoredProfile` already carry `politician_slug` + `politician_id`; do not add fields.
- Essentials HTTP access mirrors `fetch_body_roster` hardening (timeouts, 5 MB cap, `EssentialsClientError`).
- Network calls in review are **best-effort**: catch `EssentialsClientError`, degrade, never crash or block review.
- Pure logic lives in `src/` (unit-tested); `input()` loops live in `run_local.py` (manual smoke only).
- Profile identity precedence: a mapping's own `politician_slug` wins over a roster lookup.

---

## File Structure

- **`src/essentials_client.py`** (modify) — extract a shared `_request_json` helper; add `search_politicians()` + `_normalize_politician()`.
- **`src/identify.py`** (modify) — Wire 1: `match_voice_profiles` copies the matched profile's identity onto the mapping.
- **`src/enroll.py`** (modify) — Wire 2: a `_enroll_mapping` helper that keys identity-bearing speakers under `essentials:<slug>` and absorbs a local-slug profile; `enroll_speakers` + `enroll_confirmed` route through it.
- **`src/review.py`** (modify) — pure `link_speaker()`, `format_match_line()`, `parse_link_selection()`.
- **`run_local.py`** (modify) — `_prompt_link_politician()` wired into `_interactive_speaker_review`; `_enroll_after_review` routed through mapping-aware keying.
- **Tests** — extend `tests/test_essentials_client.py`, `tests/test_identification.py`, `tests/test_profile_v3.py`, `tests/test_review.py`.

---

## Task 1: `search_politicians` in the essentials client

**Files:**
- Modify: `src/essentials_client.py`
- Test: `tests/test_essentials_client.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_essentials_client.py`)

```python
# ---------------------------------------------------------------------------
# search_politicians
# ---------------------------------------------------------------------------

from src.essentials_client import search_politicians

_SAMPLE_FLAT = [
    {
        "id": "uuid-ham",
        "slug": "john-hamilton",
        "full_name": "John Hamilton",
        "office_title": "Mayor",
        "party": "Democratic",
        "district_label": "",
        "government_name": "Bloomington",
        "is_incumbent": True,
    },
    {
        "id": "uuid-can",
        "slug": "jane-doe",
        "full_name": "Jane Doe",
        "office_title": "",
        "party": "",
        "district_label": "IN-09",
        "government_name": "",
        "is_incumbent": False,
    },
]


@patch("src.essentials_client.requests.get")
def test_search_politicians_success(mock_get):
    mock_get.return_value = _mock_response(200, _SAMPLE_FLAT)
    out = search_politicians("hamilton")
    assert out[0] == {
        "politician_id": "uuid-ham",
        "politician_slug": "john-hamilton",
        "full_name": "John Hamilton",
        "office_title": "Mayor",
        "district_label": "",
        "is_incumbent": True,
        "government_name": "Bloomington",
    }
    assert out[1]["politician_slug"] == "jane-doe"
    assert out[1]["is_incumbent"] is False
    called_url = mock_get.call_args[0][0]
    assert called_url == (
        "https://accounts.empowered.vote"
        "/api/essentials/candidates/search-by-name"
    )
    assert mock_get.call_args.kwargs["params"] == {"q": "hamilton"}


@patch("src.essentials_client.requests.get")
def test_search_politicians_truncates_to_limit(mock_get):
    mock_get.return_value = _mock_response(200, _SAMPLE_FLAT)
    out = search_politicians("doe", limit=1)
    assert len(out) == 1


@patch("src.essentials_client.requests.get")
def test_search_politicians_short_query_raises(mock_get):
    with pytest.raises(EssentialsClientError) as exc_info:
        search_politicians("a")
    assert exc_info.value.code == "INVALID_QUERY"
    mock_get.assert_not_called()


@patch("src.essentials_client.requests.get")
def test_search_politicians_network_error(mock_get):
    import requests as _rq
    mock_get.side_effect = _rq.exceptions.ConnectionError("down")
    with pytest.raises(EssentialsClientError):
        search_politicians("hamilton")


@patch("src.essentials_client.requests.get")
def test_search_politicians_non_list_raises(mock_get):
    mock_get.return_value = _mock_response(200, {"oops": True})
    with pytest.raises(EssentialsClientError):
        search_politicians("hamilton")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_essentials_client.py -k search_politicians -q`
Expected: FAIL — `ImportError: cannot import name 'search_politicians'`.

- [ ] **Step 3: Extract a shared `_request_json` helper** (in `src/essentials_client.py`, add above `fetch_body_roster`)

```python
def _request_json(url: str, *, params: dict | None = None) -> object:
    """GET `url` and return parsed JSON, applying the shared hardening:
    timeouts, 5 MB cap, and EssentialsClientError on any non-200/transport/
    non-JSON failure. Used by fetch_body_roster and search_politicians.
    """
    try:
        resp = requests.get(url, params=params, timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT))
    except requests.exceptions.RequestException as exc:
        raise EssentialsClientError(f"Network error: {exc}") from exc

    content_length = resp.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_RESPONSE_BYTES:
                raise EssentialsClientError(
                    f"Response too large: {content_length} bytes "
                    f"(cap {_MAX_RESPONSE_BYTES})",
                    status=resp.status_code,
                )
        except ValueError:
            pass

    if resp.status_code == 404:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        raise EssentialsClientError(
            body.get("message", "Body not found"),
            code=body.get("code", "BODY_NOT_FOUND"),
            status=404,
        )
    if resp.status_code == 422:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        raise EssentialsClientError(
            body.get("message", "Validation error"),
            code=body.get("code", "VALIDATION_ERROR"),
            status=422,
        )
    if resp.status_code >= 500:
        raise EssentialsClientError(
            f"Server error ({resp.status_code})",
            status=resp.status_code,
        )
    if resp.status_code != 200:
        raise EssentialsClientError(
            f"Unexpected status {resp.status_code}",
            status=resp.status_code,
        )

    try:
        return resp.json()
    except ValueError as exc:  # includes requests.exceptions.JSONDecodeError
        snippet = (resp.text or "")[:200]
        raise EssentialsClientError(
            f"Non-JSON response from {url}: {snippet}",
            status=resp.status_code,
        ) from exc
```

- [ ] **Step 4: Refactor `fetch_body_roster` to use the helper** (replace its body from the `try: resp = requests.get(...)` block through the final `return resp.json()` with a single call)

```python
def fetch_body_roster(body_slug: str, base_url: str | None = None) -> dict:
    """Fetch the roster JSON for a body slug from ev-accounts.

    Raises EssentialsClientError on any non-200 status, transport failure,
    invalid slug, or oversized response.
    """
    _validate_slug(body_slug)
    base = _resolve_base_url(base_url)
    url = f"{base}/api/essentials/bodies/{body_slug}/roster"
    return _request_json(url)
```

- [ ] **Step 5: Add `search_politicians` + `_normalize_politician`** (append to `src/essentials_client.py`)

```python
def _normalize_politician(rec: dict) -> dict:
    """Whitelist the fields the pipeline needs from a PoliticianFlatRecord."""
    # No affiliation/party field — the pipeline never persists it
    # (tests/test_antipartisan.py enforces this on src/essentials_client.py).
    return {
        "politician_id": rec.get("id"),
        "politician_slug": rec.get("slug"),
        "full_name": rec.get("full_name", ""),
        "office_title": rec.get("office_title", ""),
        "district_label": rec.get("district_label", ""),
        "is_incumbent": bool(rec.get("is_incumbent", False)),
        "government_name": rec.get("government_name", ""),
    }


def search_politicians(
    q: str, *, limit: int = 10, base_url: str | None = None
) -> list[dict]:
    """Name-search essentials politicians AND candidates (one ID space).

    Calls GET /api/essentials/candidates/search-by-name (incumbents +
    challengers). Returns up to `limit` normalized dicts, each with
    politician_id, politician_slug, full_name, office_title,
    district_label, is_incumbent, government_name.

    Note: affiliation fields are intentionally excluded — the pipeline never
    reads or persists them (enforced by tests/test_antipartisan.py).

    Raises EssentialsClientError on a <2-char query (INVALID_QUERY) or any
    transport/HTTP/parse failure. Callers in review treat this as best-effort.
    """
    query = (q or "").strip()
    if len(query) < 2:
        raise EssentialsClientError(
            "Search query must be at least 2 characters",
            code="INVALID_QUERY",
            status=None,
        )
    base = _resolve_base_url(base_url)
    url = f"{base}/api/essentials/candidates/search-by-name"
    data = _request_json(url, params={"q": query})
    if not isinstance(data, list):
        raise EssentialsClientError(
            f"Expected a list from {url}, got {type(data).__name__}",
            status=None,
        )
    return [_normalize_politician(r) for r in data[:limit]]
```

- [ ] **Step 6: Run the full client test file** (proves the refactor didn't regress `fetch_body_roster`)

Run: `python -m pytest tests/test_essentials_client.py -q`
Expected: PASS (existing roster tests + 5 new search tests).

- [ ] **Step 7: Commit**

```bash
git add src/essentials_client.py tests/test_essentials_client.py
git commit -m "feat(pipeline): essentials politician/candidate name search"
```

---

## Task 2: Wire 1 — voice match carries politician identity

**Files:**
- Modify: `src/identify.py` (inside `match_voice_profiles`, the `if best_score >= threshold:` block)
- Test: `tests/test_identification.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_identification.py`)

```python
# ---------------------------------------------------------------------------
# Wire 1: match_voice_profiles propagates a matched profile's identity
# ---------------------------------------------------------------------------

import numpy as np
from src.identify import match_voice_profiles
from src.enroll import ProfileDB, StoredProfile


def _profile_db_with(identity: bool):
    centroid = np.array([1.0, 0.0, 0.0])
    prof = StoredProfile(
        speaker_id="essentials:john-hamilton" if identity else "hamilton_john",
        display_name="John Hamilton",
        embeddings=[centroid],
        centroid=centroid,
        meetings_seen=["m0"],
        politician_slug="john-hamilton" if identity else None,
        politician_id="uuid-ham" if identity else None,
    )
    return ProfileDB(profiles={prof.speaker_id: prof})


def test_voice_match_carries_identity():
    db = _profile_db_with(identity=True)
    centroids = {"essentials:john-hamilton": db.profiles["essentials:john-hamilton"].centroid}
    speaker_embeddings = {"SPEAKER_00": np.array([1.0, 0.0, 0.0])}
    out = match_voice_profiles(speaker_embeddings, centroids, profile_db=db)
    assert out["SPEAKER_00"].politician_slug == "john-hamilton"
    assert out["SPEAKER_00"].politician_id == "uuid-ham"


def test_voice_match_no_identity_stays_none():
    db = _profile_db_with(identity=False)
    centroids = {"hamilton_john": db.profiles["hamilton_john"].centroid}
    speaker_embeddings = {"SPEAKER_00": np.array([1.0, 0.0, 0.0])}
    out = match_voice_profiles(speaker_embeddings, centroids, profile_db=db)
    assert out["SPEAKER_00"].politician_slug is None
    assert out["SPEAKER_00"].politician_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_identification.py -k voice_match_carries_identity -q`
Expected: FAIL — `AssertionError` (`politician_slug` is `None`).

- [ ] **Step 3: Copy identity onto the mapping** (in `src/identify.py`, inside `match_voice_profiles`, immediately after the `mappings[label] = SpeakerMapping(...)` assignment within `if best_score >= threshold:`)

```python
                # Wire 1: carry the matched profile's politician identity so a
                # returning, already-linked speaker arrives pre-linked.
                if profile_db and best_name in profile_db.profiles:
                    prof = profile_db.profiles[best_name]
                    mappings[label].politician_slug = getattr(prof, "politician_slug", None)
                    mappings[label].politician_id = getattr(prof, "politician_id", None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_identification.py -k voice_match -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/identify.py tests/test_identification.py
git commit -m "feat(pipeline): voice match carries politician identity (Wire 1)"
```

---

## Task 3: Wire 2 — enrollment keys identity-bearing speakers under essentials:<slug>

**Files:**
- Modify: `src/enroll.py` (add `_enroll_mapping`; route `enroll_speakers` + `enroll_confirmed` through it)
- Test: `tests/test_profile_v3.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_profile_v3.py`)

```python
# ---------------------------------------------------------------------------
# Wire 2: a mapping's own identity keys it under essentials:<slug>
# ---------------------------------------------------------------------------

import numpy as np
from src.models import Segment, SpeakerMapping
from src.enroll import ProfileDB, enroll_speakers


def _seg(label):
    return Segment(segment_id=0, start_time=0.0, end_time=30.0, speaker_label=label, text="hi")


def test_mapping_identity_uses_essentials_key_without_roster():
    emb = {"SPEAKER_00": np.array([1.0, 0.0, 0.0])}
    mappings = {
        "SPEAKER_00": SpeakerMapping(
            speaker_label="SPEAKER_00",
            speaker_name="Jane Adams",
            confidence=1.0,
            politician_slug="jane-adams",
            politician_id="uuid-ja",
        )
    }
    db = enroll_speakers(ProfileDB(), emb, mappings, "m1", [_seg("SPEAKER_00")], roster=None)
    assert "essentials:jane-adams" in db.profiles
    prof = db.profiles["essentials:jane-adams"]
    assert prof.politician_slug == "jane-adams"
    assert prof.politician_id == "uuid-ja"


def test_linking_absorbs_existing_local_profile():
    emb = {"SPEAKER_00": np.array([1.0, 0.0, 0.0])}
    seg = [_seg("SPEAKER_00")]
    # Meeting 1: enrolled unlinked → local slug profile.
    m1 = {"SPEAKER_00": SpeakerMapping(
        speaker_label="SPEAKER_00", speaker_name="Jane Adams", confidence=1.0)}
    db = enroll_speakers(ProfileDB(), emb, m1, "m1", seg, roster=None)
    assert "adams_jane" in db.profiles
    # Meeting 2: now linked → must re-key to essentials and absorb the local one.
    m2 = {"SPEAKER_00": SpeakerMapping(
        speaker_label="SPEAKER_00", speaker_name="Jane Adams", confidence=1.0,
        politician_slug="jane-adams", politician_id="uuid-ja")}
    db = enroll_speakers(db, emb, m2, "m2", seg, roster=None)
    assert "essentials:jane-adams" in db.profiles
    assert "adams_jane" not in db.profiles
    prof = db.profiles["essentials:jane-adams"]
    assert prof.politician_id == "uuid-ja"
    assert len(prof.embeddings) == 2  # m1 + m2 merged
    assert set(prof.meetings_seen) == {"m1", "m2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_profile_v3.py -k "essentials_key_without_roster or absorbs_existing" -q`
Expected: FAIL — `test_mapping_identity_uses_essentials_key_without_roster` keys under `adams_jane` (roster is None, so the mapping identity is ignored today).

- [ ] **Step 3: Add `_enroll_mapping` and route both enroll functions through it** (in `src/enroll.py`)

Add this helper above `enroll_speakers`:

```python
def _enroll_mapping(
    db: ProfileDB,
    mapping: SpeakerMapping,
    embedding: np.ndarray,
    meeting_id: str,
    seg_count: int,
    roster: Optional["Roster"] = None,
) -> None:
    """Enroll one mapping, honoring its own identity before any roster lookup.

    A mapping that already carries politician_slug (from a manual link or from
    Wire 1 voice propagation) is keyed under essentials:<slug>; a pre-existing
    local-slug profile for the same display name is absorbed into it so there is
    one profile per real person. Otherwise falls back to resolve_enrollment_key.
    """
    if mapping.politician_slug:
        key = f"essentials:{mapping.politician_slug}"
        pol_slug, pol_id = mapping.politician_slug, mapping.politician_id
    else:
        key, pol_slug, pol_id = resolve_enrollment_key(mapping.speaker_name, roster)

    _enroll_one(
        db, key, mapping.speaker_name, embedding,
        meeting_id, seg_count,
        politician_slug=pol_slug, politician_id=pol_id,
    )

    # Absorb a pre-existing local-slug profile for the same display name.
    if key.startswith("essentials:") and mapping.speaker_name:
        local = _name_to_slug(mapping.speaker_name)
        if local in db.profiles and local != key:
            merge_profiles(db, local, key)
```

Then in `enroll_speakers`, replace the loop's final three lines — `slug, pol_slug, pol_id = resolve_enrollment_key(...)`, `seg_count = sum(...)`, and the `_enroll_one(...)` call — with exactly these two lines (keep the three `if ... continue` guards above them unchanged):

```python
        seg_count = sum(1 for s in segments if s.speaker_label == label)
        _enroll_mapping(db, mapping, speaker_embeddings[label], meeting_id, seg_count, roster)
```

And in `enroll_confirmed`, replace its final three lines — `slug, pol_slug, pol_id = resolve_enrollment_key(...)`, `seg_count = sum(...)`, and the `_enroll_one(...)` call — with exactly these two lines (keep the `if not mapping ... continue` and `if label not in speaker_embeddings: continue` guards above them unchanged):

```python
        seg_count = sum(1 for s in segments if s.speaker_label == label)
        _enroll_mapping(db, mapping, speaker_embeddings[label], meeting_id, seg_count, roster)
```

- [ ] **Step 4: Run the targeted tests to verify they pass**

Run: `python -m pytest tests/test_profile_v3.py -k "essentials_key_without_roster or absorbs_existing" -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the whole profile suite** (proves roster keying + reenroll tests still pass)

Run: `python -m pytest tests/test_profile_v3.py -q`
Expected: PASS (existing roster/reenroll tests unchanged + 2 new).

- [ ] **Step 6: Commit**

```bash
git add src/enroll.py tests/test_profile_v3.py
git commit -m "feat(pipeline): enroll keys linked speakers under essentials slug (Wire 2)"
```

---

## Task 4: Pure linking core + prompt helpers

**Files:**
- Modify: `src/review.py` (append three pure functions)
- Test: `tests/test_review.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_review.py`)

```python
# ---------------------------------------------------------------------------
# Politician linking core (spec 2026-06-12-speaker-politician-linking)
# ---------------------------------------------------------------------------


def test_link_speaker_sets_identity():
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Jane Adams")}
    out = review.link_speaker(mappings, "SPEAKER_00", "jane-adams", "uuid-ja")
    assert out.politician_slug == "jane-adams"
    assert out.politician_id == "uuid-ja"
    assert mappings["SPEAKER_00"].politician_slug == "jane-adams"


def test_link_speaker_clears_identity():
    mappings = {"SPEAKER_00": SpeakerMapping(
        speaker_label="SPEAKER_00", speaker_name="Jane Adams",
        politician_slug="jane-adams", politician_id="uuid-ja")}
    review.link_speaker(mappings, "SPEAKER_00", None, None)
    assert mappings["SPEAKER_00"].politician_slug is None
    assert mappings["SPEAKER_00"].politician_id is None


def test_link_speaker_creates_mapping_when_absent():
    mappings = {}
    out = review.link_speaker(mappings, "SPEAKER_09", "x-y", "uuid-xy")
    assert out.speaker_label == "SPEAKER_09"
    assert mappings["SPEAKER_09"].politician_slug == "x-y"


def test_parse_link_selection():
    assert review.parse_link_selection("", 3) == ("skip", None)
    assert review.parse_link_selection("s", 3) == ("skip", None)
    assert review.parse_link_selection("m", 3) == ("search", None)
    assert review.parse_link_selection("n", 3) == ("none", None)
    assert review.parse_link_selection("2", 3) == ("pick", 1)
    assert review.parse_link_selection("9", 3) == ("invalid", None)
    assert review.parse_link_selection("zzz", 3) == ("invalid", None)


def test_format_match_line_contains_key_facts():
    match = {
        "full_name": "John Hamilton", "office_title": "Mayor",
        "government_name": "Bloomington",
        "district_label": "", "is_incumbent": True,
    }
    line = review.format_match_line(match, 0)
    assert "1." in line
    assert "John Hamilton" in line
    assert "Mayor" in line
    assert "[incumbent]" in line


def test_format_match_line_candidate_tag():
    match = {"full_name": "Jane Doe", "office_title": "", "district_label": "IN-09",
             "government_name": "", "is_incumbent": False}
    assert "[candidate]" in review.format_match_line(match, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_review.py -k "link_speaker or parse_link or format_match" -q`
Expected: FAIL — `AttributeError: module 'src.review' has no attribute 'link_speaker'`.

- [ ] **Step 3: Implement the three pure functions** (append to `src/review.py`)

```python
def link_speaker(mappings, label, politician_slug, politician_id):
    """Set (or clear, when both are None) the politician identity on a mapping.

    Mutates `mappings` in place; returns the updated SpeakerMapping. Creates a
    bare mapping if the label has none yet.
    """
    from src.models import SpeakerMapping

    mapping = mappings.get(label) or SpeakerMapping(speaker_label=label)
    mapping.politician_slug = politician_slug
    mapping.politician_id = politician_id
    mappings[label] = mapping
    return mapping


def parse_link_selection(token, n_matches):
    """Parse the reviewer's link-prompt input.

    Returns (action, index): action in {'pick','skip','search','none','invalid'}.
    'pick' carries a 0-based index into the match list.
    """
    t = (token or "").strip().lower()
    if t in ("", "s", "skip"):
        return ("skip", None)
    if t in ("m", "search"):
        return ("search", None)
    if t in ("n", "none"):
        return ("none", None)
    if t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < n_matches:
            return ("pick", idx)
        return ("invalid", None)
    return ("invalid", None)


def format_match_line(match, index):
    """One-line rendering of a search_politicians() result for the link menu.

    No affiliation detail — the pipeline never surfaces it (antipartisan
    rule, tests/test_antipartisan.py).
    """
    tag = "incumbent" if match.get("is_incumbent") else "candidate"
    detail = []
    if match.get("office_title"):
        loc = match.get("government_name") or match.get("district_label") or ""
        detail.append(f"{match['office_title']}{', ' + loc if loc else ''}")
    elif match.get("district_label"):
        detail.append(match["district_label"])
    suffix = f" · {' · '.join(detail)}" if detail else ""
    name = match.get("full_name") or "(unknown)"
    return f"  {index + 1}. {name}{suffix} [{tag}]"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_review.py -k "link_speaker or parse_link or format_match" -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/review.py tests/test_review.py
git commit -m "feat(pipeline): pure speaker-linking core + prompt helpers"
```

---

## Task 5: Interactive link prompt wired into review

**Files:**
- Modify: `run_local.py` (add `_prompt_link_politician`; call it in `_interactive_speaker_review`'s two rename branches)

(No unit test — this is an `input()` loop over the Task 4 pure helpers. Verified by manual smoke in Step 4 and the end-to-end run in Task 7.)

- [ ] **Step 1: Add the prompt function** (in `run_local.py`, directly above `def _interactive_speaker_review`)

```python
def _prompt_link_politician(mappings: dict, label: str, query: str) -> None:
    """Offer to link a just-named speaker to an essentials politician/candidate.

    No-op when the speaker is already linked (e.g. roster auto-match) or when
    not attached to a TTY. Best-effort: any search failure degrades to a manual
    slug paste or a skip — never blocks or crashes review.
    """
    from src import review
    from src.essentials_client import EssentialsClientError, search_politicians

    mapping = mappings.get(label)
    if mapping is None or mapping.politician_slug:
        return
    if not sys.stdin.isatty():
        return

    def _do_search(q: str):
        try:
            return search_politicians(q)
        except EssentialsClientError as e:
            print(f"  (politician search unavailable: {e})")
            return None

    matches = _do_search(query)
    while True:
        if matches:
            print("  Link to a politician/candidate? (Enter = leave unlinked)")
            for i, mt in enumerate(matches):
                print(review.format_match_line(mt, i))
            prompt = "  [number] pick · [m] search again · [Enter/s] skip · [n] none/unlink: "
        else:
            prompt = "  Link politician? [m] search · [p] paste slug · [Enter/s] skip: "

        choice = input(prompt).strip()
        action, idx = review.parse_link_selection(choice, len(matches or []))

        if action == "skip":
            return
        if action == "none":
            review.link_speaker(mappings, label, None, None)
            print("  Left unlinked.")
            return
        if action == "search":
            q = input("    Search name: ").strip()
            if q:
                matches = _do_search(q)
            continue
        if action == "pick":
            mt = matches[idx]
            review.link_speaker(mappings, label, mt["politician_slug"], mt["politician_id"])
            print(f"  Linked → {mt['full_name']} ({mt['politician_slug']})")
            return
        # 'invalid' — allow a manual slug paste (handy when there are no matches).
        if choice.lower() == "p":
            slug = input("    politician_slug: ").strip()
            if slug:
                review.link_speaker(mappings, label, slug, None)
                print(f"  Linked → {slug} (id unknown)")
            return
        print("  Not understood.")
```

- [ ] **Step 2: Call it from the `[Y]`-accept branch** (in `_interactive_speaker_review`, the `elif choice.lower() in ("y", "yes") and top_hint:` block)

Replace:

```python
                elif choice.lower() in ("y", "yes") and top_hint:
                    res = review.rename_speaker(mappings, segments, label, top_hint[0], roster=roster)
                    mappings[label].id_method = "human_confirmed"
                    changes.append({"label": label, "old_name": res.old_name, "new_name": res.new_name})
                    print(f"  Confirmed: {label} -> {res.new_name}")
                    break
```

with:

```python
                elif choice.lower() in ("y", "yes") and top_hint:
                    res = review.rename_speaker(mappings, segments, label, top_hint[0], roster=roster)
                    mappings[label].id_method = "human_confirmed"
                    changes.append({"label": label, "old_name": res.old_name, "new_name": res.new_name})
                    print(f"  Confirmed: {label} -> {res.new_name}")
                    _prompt_link_politician(mappings, label, res.new_name)
                    break
```

- [ ] **Step 3: Call it from the free-typed-name branch** (the trailing `else:` block)

Replace:

```python
                else:
                    res = review.rename_speaker(mappings, segments, label, choice, roster=roster)
                    changes.append({"label": label, "old_name": res.old_name, "new_name": res.new_name})
                    print(f"  Updated: {label} -> {res.new_name}")
                    if res.alias_suggestion:
                        from src.roster import add_alias
                        if add_alias(None, res.new_name, res.alias_suggestion, body_slug=body_slug):
                            target_label = body_slug or "council_roster.json"
                            print(f"  Auto-added alias: '{res.alias_suggestion}' -> '{res.new_name}' ({target_label})")
                    break
```

with:

```python
                else:
                    res = review.rename_speaker(mappings, segments, label, choice, roster=roster)
                    changes.append({"label": label, "old_name": res.old_name, "new_name": res.new_name})
                    print(f"  Updated: {label} -> {res.new_name}")
                    if res.alias_suggestion:
                        from src.roster import add_alias
                        if add_alias(None, res.new_name, res.alias_suggestion, body_slug=body_slug):
                            target_label = body_slug or "council_roster.json"
                            print(f"  Auto-added alias: '{res.alias_suggestion}' -> '{res.new_name}' ({target_label})")
                    _prompt_link_politician(mappings, label, res.new_name)
                    break
```

- [ ] **Step 4: Byte-compile + manual smoke**

Run: `python -m py_compile run_local.py && echo OK`
Expected: `OK`.

Then, against a meeting with a reviewable speaker (substitute a real meeting id; needs `EV_ACCOUNTS_URL` reachable or it will degrade gracefully):

```bash
python run_local.py --review <MEETING_ID>
```

Walk to one speaker, type a name, then at the link menu pick a match (or `p` to paste a slug). Confirm the printed `Linked → …`. Then verify it persisted:

```bash
python - <<'PY'
import json, glob
p = sorted(glob.glob(f"{__import__('os').path.expanduser('~')}/CouncilScribe/meetings/*/transcript_named.json"))[-1]
d = json.load(open(p))
print([ (k, v.get("politician_slug")) for k,v in d["speakers"].items() ])
PY
```

Expected: the linked speaker shows a non-null `politician_slug`.

- [ ] **Step 5: Commit**

```bash
git add run_local.py
git commit -m "feat(pipeline): interactive politician link prompt in review"
```

---

## Task 6: Route post-review enrollment through mapping-aware keying

**Files:**
- Modify: `run_local.py` (`_enroll_after_review`)

(`_enroll_after_review` currently keys via `_name_to_slug` only, so a linked speaker would be enrolled under a local slug, losing the link. Route it through the Wire 2 helper so manual links land under `essentials:<slug>`.)

- [ ] **Step 1: Update the imports inside `_enroll_after_review`**

Replace:

```python
    from src.enroll import _enroll_one, _name_to_slug, load_profiles, save_profiles
```

with:

```python
    from src.enroll import _enroll_mapping, _name_to_slug, load_profiles, save_profiles
```

- [ ] **Step 2: Compute the display key from the mapping's identity** (in the `for change in changes:` loop that builds `enrollable`)

Replace:

```python
        label = change["label"]
        new_name = change["new_name"]
        if not new_name or label not in speaker_embeddings:
            continue
        slug = _name_to_slug(new_name)
        is_new = slug not in profile_db.profiles
        enrollable.append({
            "label": label,
            "name": new_name,
            "slug": slug,
            "is_new": is_new,
        })
```

with:

```python
        label = change["label"]
        new_name = change["new_name"]
        if not new_name or label not in speaker_embeddings:
            continue
        mapping = current_mappings.get(label)
        # Mapping identity (from an inline link) wins over the name-derived slug.
        if mapping is not None and mapping.politician_slug:
            slug = f"essentials:{mapping.politician_slug}"
        else:
            slug = _name_to_slug(new_name)
        is_new = slug not in profile_db.profiles
        enrollable.append({
            "label": label,
            "name": new_name,
            "slug": slug,
            "is_new": is_new,
        })
```

- [ ] **Step 3: Enroll via the mapping-aware helper** (in the `if choice in ("", "y", "yes"):` enrollment loop)

Replace:

```python
        for e in enrollable:
            mapping = current_mappings.get(e["label"])
            seg_count = sum(1 for s in segments if s.speaker_label == e["label"])
            _enroll_one(
                profile_db, e["slug"], e["name"],
                speaker_embeddings[e["label"]],
                meeting_id, seg_count,
            )
            tag = "NEW" if e["is_new"] else "UPDATE"
            print(f"  Enrolled: {e['name']} ({e['slug']}) [{tag}]")
```

with:

```python
        for e in enrollable:
            mapping = current_mappings.get(e["label"]) or SpeakerMapping(
                speaker_label=e["label"], speaker_name=e["name"])
            seg_count = sum(1 for s in segments if s.speaker_label == e["label"])
            _enroll_mapping(
                profile_db, mapping,
                speaker_embeddings[e["label"]],
                meeting_id, seg_count, None,
            )
            tag = "NEW" if e["is_new"] else "UPDATE"
            print(f"  Enrolled: {e['name']} ({e['slug']}) [{tag}]")
```

- [ ] **Step 4: Ensure `SpeakerMapping` is importable in this function**

`_enroll_after_review` already imports from `src.enroll`; add the model import at the top of the function body (next to the other local imports near `import numpy as np`):

```python
    from src.models import SpeakerMapping
```

- [ ] **Step 5: Byte-compile**

Run: `python -m py_compile run_local.py && echo OK`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add run_local.py
git commit -m "feat(pipeline): post-review enrollment honors inline politician links"
```

---

## Task 7: Full suite, end-to-end propagation check, docs

**Files:**
- Modify: `docs/web-roadmap.md` (one note under "How it works now")

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest -q`
Expected: PASS (all existing + new tests; no regressions).

- [ ] **Step 2: End-to-end propagation smoke (manual)**

Goal: prove that linking once makes a *second* meeting arrive pre-linked.

1. In one meeting's review (Task 5 smoke), link a non-roster speaker and accept enrollment when prompted.
2. Confirm the profile carries identity:

```bash
python - <<'PY'
from src.enroll import load_profiles
db = load_profiles()
for k, p in db.profiles.items():
    if k.startswith("essentials:"):
        print(k, p.politician_slug, p.politician_id, len(p.embeddings))
PY
```

Expected: an `essentials:<slug>` profile with the slug/id set.

3. Run identification on a *different* meeting where the same voice appears (or re-run `--review` on another meeting). At the speaker, the voice hint resolves to the linked profile; confirm the resulting `transcript_named.json` speaker has `politician_slug` set **without** re-linking (Wire 1).

If audio for a second real meeting isn't handy, this is acceptably covered by `tests/test_identification.py::test_voice_match_carries_identity`; note that in the execution log.

- [ ] **Step 3: Re-publish carries the link (manual, optional but recommended)**

```bash
python run_local.py --publish-meeting <MEETING_ID>
```

Then check the DB wrote it (uses the repo's `DATABASE_URL`):

```bash
python - <<'PY'
import os, psycopg
url = os.environ["DATABASE_URL"]
with psycopg.connect(url) as c, c.cursor() as cur:
    cur.execute("SELECT label, display_name, politician_slug FROM meetings.speakers WHERE politician_slug IS NOT NULL ORDER BY label LIMIT 10")
    for row in cur.fetchall():
        print(row)
PY
```

Expected: the linked speaker appears with its `politician_slug`. (Skip if not publishing during this work; `publish.py` is already covered by `tests/test_publish.py`.)

- [ ] **Step 4: Document the linking + correction flow** (in `docs/web-roadmap.md`, append to the numbered "Pipeline (this repo)" item under "How it works now")

```markdown
   During review you can link a named speaker to an essentials
   politician/candidate (`search-by-name` typeahead); the link rides the voice
   profile, so once linked a person arrives pre-linked in future meetings.
   Fix a wrong link by re-naming/unlinking in review, then re-publish; rebuild
   propagated links with `python reenroll_profiles.py`.
```

- [ ] **Step 5: Commit**

```bash
git add docs/web-roadmap.md
git commit -m "docs: note speaker→politician linking + correction flow"
```

---

## Consciously deferred (so reviewers don't think it was missed)

- **No web/admin editing surface** (Approach B/C). When wanted, it is a thin layer over operations that already exist (write `meetings.speakers`; run `reenroll`).
- **No standalone profile-edit CLI** — correction is re-name/unlink + re-publish + `reenroll_profiles.py`. A lightweight "edit one profile's identity" command is a future option if full reenroll proves too heavy.
- **No back-link pass** over already-published meetings — forward-only; re-publishing an older meeting after linking picks up the ids.
- **Linking is wired into the main interactive review only** (`_interactive_speaker_review`), not the text-only quick/batch paths — that is where naming happens.
