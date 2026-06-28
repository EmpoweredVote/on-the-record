# Auto-Link High-Confidence Matches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After speakers are named, auto-link the high-confidence ones (exactly-one strong essentials match, or a known id) with `id_method="auto_linked"` — in both the interactive and non-interactive run flows — so the unlinked-speaker backlog stops regenerating at the source.

**Architecture:** A pure `confident_target` predicate + an `auto_link_confident` pass live in `src/relink.py` (beside the existing link logic). The run flow calls the pass right before interactive review (so confident speakers are pre-linked and not re-prompted) and right after the non-interactive `human_review`. A small guard fix stops `_prompt_link_politician`/`_prompt_create_local_person` re-prompting id-linked-but-slug-null speakers. Auto-link sets identity only — publishing stays gated.

**Tech Stack:** Python 3, pytest. Run with the repo venv: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python`. Work on branch `claude/auto-link-confident` (already created off `main`; the spec is committed there).

**Spec:** `docs/superpowers/specs/2026-06-27-auto-link-confident-design.md`

**Scope guard:** Do NOT touch `src/bulk_relink.py` / `suggest_link` — the Host→Hostettler fix is owned by a separate in-flight task; D implements its own strong-match check in `confident_target`.

---

## File Structure

- **Modify `src/relink.py`** — add `_is_strong_name_match`, `confident_target`, `auto_link_confident` (beside `resolve_link_target`/`relink_in_meeting`).
- **Modify `run_local.py`** — fix the `politician_slug`-only "already linked" guard in `_prompt_link_politician` (~line 2509) and `_prompt_create_local_person` (~line 2572) to also honor `politician_id`; wire `auto_link_confident` into the main run's review branch (~line 1277).
- **Test `tests/test_relink.py`** — `_is_strong_name_match`, `confident_target`, `auto_link_confident`.
- **Test `tests/test_auto_link_guard.py`** (new, or append to an existing run_local test) — the guard fix.

---

## Task 1: `_is_strong_name_match` + `confident_target`

**Files:**
- Modify: `src/relink.py` (after `resolve_link_target`)
- Test: `tests/test_relink.py`

Context: `src/relink.py` has `@dataclass ResolvedTarget(politician_id, politician_slug, full_name)`, `from src.essentials_client import EssentialsClientError, search_politicians`, and uses `.strip().lower()` for name normalization. `search_politicians(q)` returns normalized dicts with `politician_id`, `politician_slug`, `full_name`.

- [ ] **Step 1: Write the failing tests** — Append to `tests/test_relink.py`:

```python
from src.essentials_client import EssentialsClientError
from src.relink import _is_strong_name_match, confident_target


def test_is_strong_name_match():
    assert _is_strong_name_match("Steve Hilton", "Steve Hilton") is True
    assert _is_strong_name_match("steyer", "Tom Steyer") is True
    assert _is_strong_name_match("Katie Porter", "Katie Porter") is True
    assert _is_strong_name_match("Host", "Matthew Hostettler") is False      # substring, not a token
    assert _is_strong_name_match("Councilmember Rollo", "David R Rollo") is False  # prefix not a token
    assert _is_strong_name_match("Steve A Hilton", "Steve Hilton") is False   # extra token
    assert _is_strong_name_match("", "Whoever") is False


def test_confident_target_single_strong_match():
    cands = [_cand("uuid-1", "steve-hilton", "Steve Hilton")]
    t = confident_target("Steve Hilton", search=lambda q, **kw: cands)
    assert t == ResolvedTarget("uuid-1", "steve-hilton", "Steve Hilton")


def test_confident_target_substring_only_is_none():
    cands = [_cand("uuid-h", None, "Matthew Hostettler")]
    assert confident_target("Host", search=lambda q, **kw: cands) is None


def test_confident_target_multiple_is_none():
    cands = [_cand("u1", None, "John Smith"), _cand("u2", None, "John Smith")]
    assert confident_target("John Smith", search=lambda q, **kw: cands) is None


def test_confident_target_zero_is_none():
    assert confident_target("Nobody", search=lambda q, **kw: []) is None


def test_confident_target_known_id_wins():
    cands = [_cand("uuid-h", "hilton", "Steve Hilton")]
    t = confident_target("Steve Hilton", search=lambda q, **kw: cands, known_id="uuid-h")
    assert t.politician_id == "uuid-h"


def test_confident_target_api_error_is_none():
    def boom(q, **kw):
        raise EssentialsClientError("down")
    assert confident_target("Steve Hilton", search=boom) is None
```

Note: `_cand`, `ResolvedTarget`, `_meeting`, and `SpeakerMapping` are already defined/imported at module level in `tests/test_relink.py`. `EssentialsClientError` is NOT module-level there (it's imported locally inside some existing tests), so the block above adds the `from src.essentials_client import EssentialsClientError` import — keep it.

- [ ] **Step 2: Run to verify it fails** — Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_relink.py -k "strong_name or confident_target" -v` — Expected: FAIL with `ImportError: cannot import name '_is_strong_name_match'`.

- [ ] **Step 3: Implement** — In `src/relink.py`, add after `resolve_link_target`:

```python
def _is_strong_name_match(name: str, full_name: str) -> bool:
    """True when every whitespace token of `name` is a whole-word token of
    `full_name` (case-insensitive). Rejects substring fuzz ("Host" vs
    "Hostettler") and title prefixes ("Councilmember Rollo" vs "David R Rollo")."""
    name_tokens = set((name or "").lower().split())
    cand_tokens = set((full_name or "").lower().split())
    return bool(name_tokens) and name_tokens.issubset(cand_tokens)


def confident_target(
    name, *, search=search_politicians, known_id: Optional[str] = None
) -> Optional[ResolvedTarget]:
    """Resolve a name to a politician ONLY when confident enough to auto-link.

    known_id set -> that target (already linked elsewhere; highest confidence;
    slug/name filled best-effort from a search hit). Otherwise: exactly one
    search match AND a strong name match -> that target; zero/multiple/weak ->
    None. EssentialsClientError -> None (best-effort; never blocks a run).
    """
    if known_id:
        slug, full = None, name
        try:
            for m in search(name):
                if m.get("politician_id") == known_id:
                    slug = m.get("politician_slug")
                    full = m.get("full_name") or name
                    break
        except EssentialsClientError:
            pass
        return ResolvedTarget(known_id, slug, full)

    try:
        matches = search(name)
    except EssentialsClientError:
        return None
    if len(matches) == 1 and _is_strong_name_match(name, matches[0].get("full_name") or ""):
        m = matches[0]
        return ResolvedTarget(m["politician_id"], m.get("politician_slug"), m.get("full_name") or name)
    return None
```

(`Optional` is already imported in `src/relink.py`.)

- [ ] **Step 4: Run to verify it passes** — same pytest command — Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
git add src/relink.py tests/test_relink.py
git commit -m "feat(auto-link): confident_target + strong-name-match predicate"
```

---

## Task 2: `auto_link_confident` pass

**Files:**
- Modify: `src/relink.py` (after `confident_target`)
- Test: `tests/test_relink.py`

Context: `relink_in_meeting` (same file) uses `from src.review import link_speaker; link_speaker(mappings, label, slug, id)` which sets both politician fields. `SpeakerMapping` has `speaker_name`, `politician_id`, `speaker_status` (None / 'unidentified' / 'non_speaker'), `local_slug`, `id_method`. **Verify `review.link_speaker` does not overwrite `id_method`** — set `id_method="auto_linked"` AFTER calling it.

- [ ] **Step 1: Write the failing tests** — Append to `tests/test_relink.py`:

```python
from src.relink import auto_link_confident


def _strong_search(q, **kw):
    # one strong match for "Steve Hilton", nothing else
    return [_cand("uuid-h", "steve-hilton", "Steve Hilton")] if q.strip().lower() == "steve hilton" else []


def test_auto_link_confident_links_named_unlinked():
    m = _meeting({
        "S0": SpeakerMapping(speaker_label="S0", speaker_name="Steve Hilton"),
        "S1": SpeakerMapping(speaker_label="S1", speaker_name="Nobody Here"),
    })
    linked = auto_link_confident(m.speakers, search=_strong_search)
    assert linked == ["S0"]
    assert m.speakers["S0"].politician_id == "uuid-h"
    assert m.speakers["S0"].id_method == "auto_linked"
    assert m.speakers["S1"].politician_id is None       # no confident match


def test_auto_link_confident_skips_already_linked_and_special():
    m = _meeting({
        "S0": SpeakerMapping(speaker_label="S0", speaker_name="Steve Hilton", politician_id="x"),
        "S1": SpeakerMapping(speaker_label="S1", speaker_name="Steve Hilton", speaker_status="unidentified"),
        "S2": SpeakerMapping(speaker_label="S2", speaker_name="Steve Hilton", local_slug="local-steve"),
        "S3": SpeakerMapping(speaker_label="S3", speaker_name=None),
    })
    linked = auto_link_confident(m.speakers, search=_strong_search)
    assert linked == []                                  # none eligible
    assert m.speakers["S0"].id_method != "auto_linked"   # pre-linked untouched
```

(`_meeting` and `SpeakerMapping` are already imported in `tests/test_relink.py`.)

- [ ] **Step 2: Run to verify it fails** — Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_relink.py -k auto_link_confident -v` — Expected: FAIL with `ImportError: cannot import name 'auto_link_confident'`.

- [ ] **Step 3: Implement** — In `src/relink.py`, add after `confident_target`:

```python
def auto_link_confident(mappings, *, search=search_politicians) -> list[str]:
    """Auto-link every named-but-unlinked speaker with a confident match.

    Considers a mapping only when speaker_name is set, politician_id is None,
    speaker_status is normal (not 'unidentified'/'non_speaker'), and local_slug
    is None. On a confident_target hit, links it and marks id_method='auto_linked'
    (distinct, auditable, reversible). Returns the labels auto-linked.
    """
    from src.review import link_speaker

    linked: list[str] = []
    for label, mapping in list(mappings.items()):
        if not mapping.speaker_name:
            continue
        if mapping.politician_id is not None:
            continue
        if mapping.speaker_status in ("unidentified", "non_speaker"):
            continue
        if mapping.local_slug is not None:
            continue
        target = confident_target(mapping.speaker_name, search=search)
        if target is None:
            continue
        link_speaker(mappings, label, target.politician_slug, target.politician_id)
        mapping.id_method = "auto_linked"
        linked.append(label)
    return linked
```

- [ ] **Step 4: Run to verify it passes** — same pytest command — Expected: PASS (2 tests). If `link_speaker` turns out to overwrite `id_method`, set `mapping.id_method = "auto_linked"` strictly after it (the order above already does); re-run.

- [ ] **Step 5: Commit**

```bash
git add src/relink.py tests/test_relink.py
git commit -m "feat(auto-link): auto_link_confident pass (id_method=auto_linked)"
```

---

## Task 3: Guard fix — honor `politician_id` in the link prompts

**Files:**
- Modify: `run_local.py` — `_prompt_link_politician` (~line 2509) and `_prompt_create_local_person` (~line 2572).
- Test: `tests/test_auto_link_guard.py` (new)

Context: both functions early-return when a speaker is "already linked", but they test only `mapping.politician_slug`. Id-keyed links (incl. auto-links) have `politician_slug=None`, so without this fix the prompts re-fire for auto-linked speakers. The current guard line in BOTH is:

```python
    if mapping is None or mapping.politician_slug:
        return
```

- [ ] **Step 1: Write the failing test** — Create `tests/test_auto_link_guard.py`:

```python
from __future__ import annotations

import run_local
from src.models import SpeakerMapping


def test_prompt_link_skips_when_politician_id_set_slug_null(monkeypatch, capsys):
    # An id-linked (slug-null) speaker must be treated as already linked: the
    # prompt returns immediately without searching or prompting.
    called = {"search": 0}
    monkeypatch.setattr("src.essentials_client.search_politicians",
                        lambda *a, **k: called.__setitem__("search", called["search"] + 1) or [])
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Steve Hilton",
                                     politician_id="uuid-h", politician_slug=None)}
    run_local._prompt_link_politician(mappings, "S0", "Steve Hilton")
    assert called["search"] == 0   # short-circuited; never searched/prompted
```

- [ ] **Step 2: Run to verify it fails** — Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_auto_link_guard.py -v` — Expected: FAIL (current guard checks only `politician_slug`, which is None, so it proceeds and calls `search_politicians`, making `called["search"] == 1`).

- [ ] **Step 3: Fix both guards** — In `run_local.py`, change the guard in BOTH `_prompt_link_politician` (~line 2509) and `_prompt_create_local_person` (~line 2572) from:

```python
    if mapping is None or mapping.politician_slug:
        return
```

to:

```python
    if mapping is None or mapping.politician_slug or mapping.politician_id:
        return
```

(Both functions should treat an essentials-linked speaker — by slug OR id — as already linked. `_prompt_create_local_person` likewise must not offer a local person for an id-linked speaker.)

- [ ] **Step 4: Run to verify it passes** — same pytest command — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add run_local.py tests/test_auto_link_guard.py
git commit -m "fix(auto-link): link/local-person prompts honor politician_id (not just slug)"
```

---

## Task 4: Wire `auto_link_confident` into the run flow

**Files:**
- Modify: `run_local.py` — the main run's review branch (~lines 1277-1288).
- Test: structural verification (the linking logic is fully unit-tested in Tasks 1–2; the full pipeline run isn't unit-testable here).

Context: the main run, after naming, has:

```python
        if sys.stdin.isatty() and not getattr(args, "no_review", False):
            review_video = find_video_file(meeting_dir, meeting.audio_source)
            review_changes = _interactive_speaker_review(
                segments, mappings, speaker_embeddings, profile_db,
                review_video, str(wav_path),
                roster=roster, body_slug=effective_body_slug, show_text=True,
                event_kind=meeting.event_kind,
                meeting_id=meeting_dir.name,
            )
            _persist_after_review(meeting_dir, segments, speaker_embeddings, review_changes)
        else:
            mappings = human_review(mappings)
```

Auto-link must run BEFORE interactive review (so confident speakers are pre-linked and `_prompt_link_politician` — fixed in Task 3 — skips them) and AFTER `human_review` in the non-interactive branch (so the final mappings carry the links). Two placements avoid any dependence on `human_review`'s return behavior and never override an operator's in-review decision.

- [ ] **Step 1: Implement the wiring** — Replace the block above with:

```python
        from src.relink import auto_link_confident

        if sys.stdin.isatty() and not getattr(args, "no_review", False):
            auto = auto_link_confident(mappings)
            if auto:
                print(f"  Auto-linked {len(auto)} confident speaker(s) before review: "
                      f"{', '.join(auto)}")
            review_video = find_video_file(meeting_dir, meeting.audio_source)
            review_changes = _interactive_speaker_review(
                segments, mappings, speaker_embeddings, profile_db,
                review_video, str(wav_path),
                roster=roster, body_slug=effective_body_slug, show_text=True,
                event_kind=meeting.event_kind,
                meeting_id=meeting_dir.name,
            )
            _persist_after_review(meeting_dir, segments, speaker_embeddings, review_changes)
        else:
            mappings = human_review(mappings)
            auto = auto_link_confident(mappings)
            if auto:
                print(f"  Auto-linked {len(auto)} confident speaker(s): {', '.join(auto)}")
```

- [ ] **Step 2: Structural verification** — Run:
```
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -c "import run_local" && grep -n "auto_link_confident" /Users/chrisandrews/Documents/GitHub/on-the-record/run_local.py
```
Expected: import OK; `auto_link_confident` appears in both the interactive (before `_interactive_speaker_review`) and the non-interactive (after `human_review`) branches.

- [ ] **Step 3: Commit**

```bash
git add run_local.py
git commit -m "feat(auto-link): run auto_link_confident before review / after human_review"
```

---

## Task 5: Full regression

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q`
Expected: all tests pass (was 540 before this plan; this adds ~10 tests).

- [ ] **Step 2: Commit any fixups** (skip if already green)

```bash
git add -u && git commit -m "test(auto-link): align tests"
```

---

## Self-Review Notes (reconciled against the spec)

- **Spec coverage:** strong-match predicate (Task 1 `_is_strong_name_match`) · `confident_target` with known-id + exactly-one-strong + API-error→None (Task 1) · `auto_link_confident` setting `id_method="auto_linked"`, skipping linked/unidentified/non_speaker/local (Task 2) · guard fix so auto-linked (slug-null) speakers aren't re-prompted (Task 3) · wiring into interactive (pre-review) + non-interactive (post-human_review) run flows (Task 4) · regression (Task 5). Does NOT auto-publish (no publish changes). Does NOT touch `suggest_link`/`bulk_relink.py` (in-flight Host task owns it). Standalone `--review`/`--identify` auto-link deferred (per spec out-of-scope).
- **Type/name consistency:** `_is_strong_name_match(name, full_name) -> bool`, `confident_target(name, *, search, known_id) -> Optional[ResolvedTarget]`, `auto_link_confident(mappings, *, search) -> list[str]`, and `id_method="auto_linked"` are used consistently across tasks; `ResolvedTarget`/`_cand`/`_meeting`/`SpeakerMapping` reuse the existing `tests/test_relink.py` fixtures.
