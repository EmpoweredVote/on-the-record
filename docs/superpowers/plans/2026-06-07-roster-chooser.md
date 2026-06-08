# Roster Chooser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `run_local.py` runs interactively without `--body`, prompt the operator to pick a council roster (cached per-body, legacy file, or none) instead of silently defaulting to the legacy Bloomington roster.

**Architecture:** Add a persisted `roster_choice` field to `PipelineState`. In `run_pipeline`, at the existing Phase 109 body-resolution point, call an interactive chooser when no roster is specified. A cached pick sets `body_slug` (tagging the meeting like `--body`); legacy/none are recorded via `__legacy__`/`__none__` sentinels. Stage 4 resolves the roster through one pure helper. Pure helpers (`_should_prompt_roster`, `_resolve_roster`, `_list_cached_rosters`, `_prompt_roster_choice`) keep the logic unit-testable without the GPU pipeline.

**Tech Stack:** Python 3, pytest, existing `src/checkpoint.py` + `src/roster.py` + `run_local.py`.

Spec: `docs/superpowers/specs/2026-06-07-roster-chooser-design.md`

---

## File Structure

- `src/checkpoint.py` — `PipelineState` gains a `roster_choice` field (init, `_load`, `save`).
- `run_local.py` — four new module-level helpers + wiring into `run_pipeline` (body-resolution block and Stage 4 load).
- `tests/test_roster_chooser.py` — new test file for all chooser/state/helper logic.
- `tests/test_body_tagging.py` — update the now-outdated D-05 `test_legacy_fallback_intact` test.
- `README.md` — roster-selection note under "Speaker identification strategy".

Helper signatures (defined once, referenced across tasks):

```python
# run_local.py
def _list_cached_rosters() -> list[tuple[str, str]]: ...          # [(slug, label), ...]
def _prompt_roster_choice() -> tuple[Optional[str], str]: ...     # (body_slug_or_None, marker)
def _should_prompt_roster(*, cli_body, persisted_body, roster_choice, identified, isatty) -> bool: ...
def _resolve_roster(effective_body_slug: Optional[str], roster_choice: Optional[str]): ...  # -> Optional[Roster]
```

Sentinel values stored in `state.roster_choice`: a real slug (cached pick), `"__legacy__"`, or `"__none__"`. `None` means "not yet chosen".

---

## Task 1: Persist `roster_choice` on PipelineState

**Files:**
- Modify: `src/checkpoint.py:29-61`
- Test: `tests/test_roster_chooser.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_roster_chooser.py` with:

```python
"""Tests for the run_local.py roster chooser (spec 2026-06-07-roster-chooser-design)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.checkpoint import PipelineState, PipelineStage


def test_roster_choice_defaults_to_none(tmp_path):
    state = PipelineState(tmp_path)
    assert state.roster_choice is None


def test_roster_choice_roundtrips_through_save(tmp_path):
    state = PipelineState(tmp_path)
    state.roster_choice = "__none__"
    state.save()

    reloaded = PipelineState(tmp_path)
    assert reloaded.roster_choice == "__none__"


def test_roster_choice_persisted_in_json(tmp_path):
    state = PipelineState(tmp_path)
    state.roster_choice = "bloomington-common-council"
    state.save()

    data = json.loads((tmp_path / "pipeline_state.json").read_text())
    assert data["roster_choice"] == "bloomington-common-council"


def test_legacy_state_file_without_roster_choice_loads_as_none(tmp_path):
    # State file written before this feature existed (no roster_choice key).
    (tmp_path / "pipeline_state.json").write_text(json.dumps({
        "completed_stage": 3,
        "transcription_progress": 0,
        "total_segments": 0,
        "body_slug": None,
    }))
    state = PipelineState(tmp_path)
    assert state.roster_choice is None
    assert state.completed_stage == PipelineStage.TRANSCRIBED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_roster_chooser.py -v`
Expected: FAIL — `AttributeError: 'PipelineState' object has no attribute 'roster_choice'`

- [ ] **Step 3: Add the field to PipelineState**

In `src/checkpoint.py`, in `__init__` (after the `self.body_slug = None` line):

```python
        self.body_slug: Optional[str] = None
        self.roster_choice: Optional[str] = None  # None=unchosen, slug, "__legacy__", or "__none__"
        self._load()
```

In `_load`, after the `self.body_slug = data.get("body_slug")` line:

```python
            self.body_slug = data.get("body_slug")  # None if legacy/untagged — D-05 compat
            self.roster_choice = data.get("roster_choice")  # None for pre-chooser state files
```

In `save`, add `roster_choice` to the `data` dict:

```python
        data = {
            "completed_stage": int(self.completed_stage),
            "transcription_progress": self.transcription_progress,
            "total_segments": self.total_segments,
            "body_slug": self.body_slug,
            "roster_choice": self.roster_choice,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_roster_chooser.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Verify existing checkpoint tests still pass**

Run: `python -m pytest tests/test_body_tagging.py -v`
Expected: PASS (no regressions; `rewind_for_retag`/`save` untouched in behavior)

- [ ] **Step 6: Commit**

```bash
git add src/checkpoint.py tests/test_roster_chooser.py
git commit -m "feat(checkpoint): persist roster_choice on PipelineState"
```

---

## Task 2: `_list_cached_rosters` helper

**Files:**
- Modify: `run_local.py` (add helper near the other roster utilities, e.g. after `ensure_body_roster_cached`)
- Test: `tests/test_roster_chooser.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_roster_chooser.py`:

```python
def _write_cache(rosters_dir: Path, slug: str, body_key: str, n_members: int) -> None:
    rosters_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "body_slug": slug,
        "body_key": body_key,
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "politicians": [{"full_name": f"Member {i}", "title": "Councilmember"} for i in range(n_members)],
    }
    (rosters_dir / f"{slug}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_list_cached_rosters_empty(tmp_config_dir):
    import run_local
    assert run_local._list_cached_rosters() == []


def test_list_cached_rosters_returns_sorted_slug_and_label(tmp_config_dir):
    import run_local
    rosters = tmp_config_dir / "rosters"
    _write_cache(rosters, "zzz-town-council", "ZZZ Town Council", 3)
    _write_cache(rosters, "aaa-city-council", "AAA City Council", 5)

    result = run_local._list_cached_rosters()

    # sorted by slug (filename)
    assert [slug for slug, _ in result] == ["aaa-city-council", "zzz-town-council"]
    assert result[0][1] == "AAA City Council (5 members) [aaa-city-council]"
    assert result[1][1] == "ZZZ Town Council (3 members) [zzz-town-council]"
```

`tmp_config_dir` comes from `tests/conftest.py` and monkeypatches `src.config.CONFIG_DIR`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_roster_chooser.py -k list_cached -v`
Expected: FAIL — `AttributeError: module 'run_local' has no attribute '_list_cached_rosters'`

- [ ] **Step 3: Implement the helper**

In `run_local.py`, add after the `ensure_body_roster_cached` function:

```python
def _list_cached_rosters() -> list[tuple[str, str]]:
    """Return [(body_slug, label), ...] for each cached per-body roster.

    Scans CONFIG_DIR/rosters/*.json, sorted by filename. label is
    "{body_key} ({N} members) [{slug}]", falling back to the slug if the
    file can't be parsed.
    """
    rosters_dir = config.CONFIG_DIR / "rosters"
    out: list[tuple[str, str]] = []
    if not rosters_dir.exists():
        return out
    for path in sorted(rosters_dir.glob("*.json")):
        slug = path.stem
        label = slug
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            body_key = data.get("body_key") or slug
            count = len(data.get("politicians", []))
            label = f"{body_key} ({count} members) [{slug}]"
        except Exception:
            pass
        out.append((slug, label))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_roster_chooser.py -k list_cached -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add run_local.py tests/test_roster_chooser.py
git commit -m "feat(run_local): add _list_cached_rosters helper"
```

---

## Task 3: `_should_prompt_roster` helper

**Files:**
- Modify: `run_local.py` (add helper near `_list_cached_rosters`)
- Test: `tests/test_roster_chooser.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_roster_chooser.py`:

```python
@pytest.mark.parametrize("kwargs,expected", [
    # The only "prompt" case: interactive, no cli body, no persisted body,
    # no prior choice, not yet identified.
    (dict(cli_body=None, persisted_body=None, roster_choice=None, identified=False, isatty=True), True),
    # --body given → never prompt
    (dict(cli_body="x", persisted_body=None, roster_choice=None, identified=False, isatty=True), False),
    # already tagged → never prompt
    (dict(cli_body=None, persisted_body="x", roster_choice=None, identified=False, isatty=True), False),
    # prior choice recorded → never prompt
    (dict(cli_body=None, persisted_body=None, roster_choice="__none__", identified=False, isatty=True), False),
    # identification already complete → never prompt
    (dict(cli_body=None, persisted_body=None, roster_choice=None, identified=True, isatty=True), False),
    # not a terminal → never prompt
    (dict(cli_body=None, persisted_body=None, roster_choice=None, identified=False, isatty=False), False),
])
def test_should_prompt_roster(kwargs, expected):
    import run_local
    assert run_local._should_prompt_roster(**kwargs) is expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_roster_chooser.py -k should_prompt -v`
Expected: FAIL — `AttributeError: module 'run_local' has no attribute '_should_prompt_roster'`

- [ ] **Step 3: Implement the helper**

In `run_local.py`, add after `_list_cached_rosters`:

```python
def _should_prompt_roster(
    *,
    cli_body,
    persisted_body,
    roster_choice,
    identified: bool,
    isatty: bool,
) -> bool:
    """Decide whether to show the interactive roster chooser.

    Prompt only on a fresh interactive run where the operator hasn't already
    chosen a roster: TTY attached, no --body, no persisted body_slug, no prior
    roster_choice, and Stage 4 (identification) not already complete.
    """
    return (
        isatty
        and not cli_body
        and not persisted_body
        and roster_choice is None
        and not identified
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_roster_chooser.py -k should_prompt -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add run_local.py tests/test_roster_chooser.py
git commit -m "feat(run_local): add _should_prompt_roster gate helper"
```

---

## Task 4: `_prompt_roster_choice` interactive menu

**Files:**
- Modify: `run_local.py` (add helper near `_list_cached_rosters`)
- Test: `tests/test_roster_chooser.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_roster_chooser.py`:

```python
def _setup_menu(tmp_config_dir, *, legacy=False):
    rosters = tmp_config_dir / "rosters"
    _write_cache(rosters, "bloomington-common-council", "Bloomington Common Council", 10)
    if legacy:
        (tmp_config_dir / "council_roster.json").write_text(json.dumps({
            "city": "Bloomington", "body": "City Council",
            "members": [{"name": f"Councilmember {i}"} for i in range(8)],
        }), encoding="utf-8")


def test_prompt_pick_cached_roster(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=True)
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug == "bloomington-common-council"
    assert marker == "bloomington-common-council"


def test_prompt_pick_legacy(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=True)
    # Menu: 1=cached, 2=legacy, 3=no roster
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug is None
    assert marker == "__legacy__"


def test_prompt_pick_no_roster(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=True)
    monkeypatch.setattr("builtins.input", lambda *a: "3")
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug is None
    assert marker == "__none__"


def test_prompt_bare_enter_defaults_to_no_roster(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=True)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug is None
    assert marker == "__none__"


def test_prompt_reprompts_on_bad_input(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=False)  # menu: 1=cached, 2=no roster
    answers = iter(["banana", "9", "1"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug == "bloomington-common-council"
    assert marker == "bloomington-common-council"


def test_prompt_no_legacy_present(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=False)  # menu: 1=cached, 2=no roster
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug is None
    assert marker == "__none__"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_roster_chooser.py -k prompt -v`
Expected: FAIL — `AttributeError: module 'run_local' has no attribute '_prompt_roster_choice'`

- [ ] **Step 3: Implement the helper**

In `run_local.py`, add after `_should_prompt_roster`:

```python
def _prompt_roster_choice() -> tuple[Optional[str], str]:
    """Interactive roster chooser. Returns (body_slug_or_None, marker).

    marker is the value to persist in state.roster_choice:
      - the slug itself for a cached roster (body_slug is also returned)
      - "__legacy__" for the legacy council_roster.json
      - "__none__" for no roster (also the bare-Enter default)

    Caller is responsible for only invoking this when interactive
    (see _should_prompt_roster).
    """
    cached = _list_cached_rosters()
    legacy_path = config.CONFIG_DIR / "council_roster.json"
    has_legacy = legacy_path.exists()

    print("=" * 60)
    print("ROSTER SELECTION")
    print("=" * 60)
    print("  Which council roster should guide speaker identification?")
    print()

    # options[i] = ("cached"|"legacy"|"none", slug_or_None)
    options: list[tuple[str, Optional[str]]] = []
    n = 0
    for slug, label in cached:
        n += 1
        print(f"  {n}. {label}")
        options.append(("cached", slug))

    if has_legacy:
        legacy_label = "legacy council_roster.json"
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            name = f"{data.get('city', '')} {data.get('body', '')}".strip()
            members = len(data.get("members", []))
            legacy_label = f"{name or 'council_roster.json'} (legacy, {members} members)"
        except Exception:
            pass
        n += 1
        print(f"  {n}. {legacy_label}")
        options.append(("legacy", None))

    n += 1
    none_index = n
    print(f"  {n}. No roster (skip name correction)")
    options.append(("none", None))
    print()

    while True:
        choice = input(f"  Select [1-{n}] (default {none_index} = no roster): ").strip()
        if choice == "":
            kind, value = "none", None
            break
        try:
            idx = int(choice)
        except ValueError:
            print("  Please enter a number.")
            continue
        if 1 <= idx <= len(options):
            kind, value = options[idx - 1]
            break
        print(f"  Out of range. Enter 1-{n}.")

    if kind == "cached":
        return value, value
    if kind == "legacy":
        return None, "__legacy__"
    return None, "__none__"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_roster_chooser.py -k prompt -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add run_local.py tests/test_roster_chooser.py
git commit -m "feat(run_local): add interactive _prompt_roster_choice menu"
```

---

## Task 5: `_resolve_roster` helper + new Stage 4 contract

**Files:**
- Modify: `run_local.py` (add helper near the others)
- Test: `tests/test_roster_chooser.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_roster_chooser.py`:

```python
from unittest.mock import patch


def test_resolve_roster_body_slug_loads_body_specific(tmp_config_dir):
    import run_local
    sentinel = object()
    calls = []

    def fake_load_roster(path=None, *, body_slug=None):
        calls.append({"path": path, "body_slug": body_slug})
        return sentinel

    with patch("src.roster.load_roster", side_effect=fake_load_roster):
        roster = run_local._resolve_roster("bloomington-common-council", None)

    assert roster is sentinel
    assert calls == [{"path": None, "body_slug": "bloomington-common-council"}]


def test_resolve_roster_legacy_calls_bare_load(tmp_config_dir):
    import run_local
    sentinel = object()
    calls = []

    def fake_load_roster(path=None, *, body_slug=None):
        calls.append({"path": path, "body_slug": body_slug})
        return sentinel

    with patch("src.roster.load_roster", side_effect=fake_load_roster):
        roster = run_local._resolve_roster(None, "__legacy__")

    assert roster is sentinel
    assert calls == [{"path": None, "body_slug": None}]  # bare load_roster()


def test_resolve_roster_none_does_not_load(tmp_config_dir):
    import run_local
    calls = []

    def fake_load_roster(path=None, *, body_slug=None):
        calls.append(1)
        return object()

    with patch("src.roster.load_roster", side_effect=fake_load_roster):
        roster = run_local._resolve_roster(None, "__none__")

    assert roster is None
    assert calls == []  # never touches the roster loader


def test_resolve_roster_unchosen_defaults_to_no_roster(tmp_config_dir):
    """Non-interactive / no --body / no choice → no roster (the D3 behavior flip)."""
    import run_local
    calls = []

    def fake_load_roster(path=None, *, body_slug=None):
        calls.append(1)
        return object()

    with patch("src.roster.load_roster", side_effect=fake_load_roster):
        roster = run_local._resolve_roster(None, None)

    assert roster is None
    assert calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_roster_chooser.py -k resolve_roster -v`
Expected: FAIL — `AttributeError: module 'run_local' has no attribute '_resolve_roster'`

- [ ] **Step 3: Implement the helper**

In `run_local.py`, add after `_prompt_roster_choice`:

```python
def _resolve_roster(effective_body_slug: Optional[str], roster_choice: Optional[str]):
    """Resolve the Roster (or None) for Stage 4 given the meeting's state.

    - body_slug set      → load that body's cached roster.
    - roster_choice legacy → bare load_roster() (legacy council_roster.json).
    - "__none__" / unchosen → no roster (no name correction). This is the
      non-interactive default since the chooser only runs interactively.
    """
    from src.roster import load_roster

    if effective_body_slug:
        return load_roster(body_slug=effective_body_slug)
    if roster_choice == "__legacy__":
        return load_roster()
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_roster_chooser.py -k resolve_roster -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Wire `_resolve_roster` into Stage 4**

In `run_local.py`, change the Stage 4 import line (currently `from src.roster import load_roster, roster_names_for_prompt`) to drop the now-unused `load_roster`:

```python
    from src.roster import roster_names_for_prompt
```

Then replace the if/else roster-load block:

```python
    if effective_body_slug:
        roster = load_roster(body_slug=effective_body_slug)
    else:
        roster = load_roster()  # D-05 legacy fallback
    if roster:
        # Roster dataclass may not have .city/.body when loaded from a body-keyed cache;
        # print whichever label is available without crashing the legacy path.
        label = f"{getattr(roster, 'city', '') or ''} {getattr(roster, 'body', '') or ''}".strip()
        if not label and effective_body_slug:
            label = effective_body_slug
        print(f"  Loaded council roster: {len(roster.members)} members ({label})")
    roster_hint = roster_names_for_prompt(roster) if roster else ""
```

with:

```python
    roster = _resolve_roster(effective_body_slug, state.roster_choice)
    if roster:
        # Roster dataclass may not have .city/.body when loaded from a body-keyed cache;
        # print whichever label is available without crashing the legacy path.
        label = f"{getattr(roster, 'city', '') or ''} {getattr(roster, 'body', '') or ''}".strip()
        if not label and effective_body_slug:
            label = effective_body_slug
        print(f"  Loaded council roster: {len(roster.members)} members ({label})")
    else:
        print("  No roster loaded — speaker names won't be corrected against a council roster.")
    roster_hint = roster_names_for_prompt(roster) if roster else ""
```

- [ ] **Step 6: Verify the module still imports cleanly**

Run: `python -c "import run_local"`
Expected: no output, exit 0 (no NameError from the dropped `load_roster` import).

- [ ] **Step 7: Commit**

```bash
git add run_local.py tests/test_roster_chooser.py
git commit -m "feat(run_local): resolve Stage 4 roster via _resolve_roster (no-roster default)"
```

---

## Task 6: Wire the chooser into run_pipeline + update D-05 test

**Files:**
- Modify: `run_local.py:315-317` (body-resolution block, before `effective_body_slug = state.body_slug`)
- Modify: `tests/test_body_tagging.py:306-334` (`test_legacy_fallback_intact`)

- [ ] **Step 1: Update the outdated D-05 test to the new contract**

In `tests/test_body_tagging.py`, replace the whole `test_legacy_fallback_intact` function with:

```python
def test_no_body_no_choice_uses_no_roster(tmp_path, tmp_config_dir, tmp_meetings_dir):
    """Updated D-05: no --body, no persisted slug, no roster_choice → NO roster.

    The pre-chooser contract returned the legacy council_roster.json here.
    As of the roster-chooser change (spec 2026-06-07), a non-interactive run
    with nothing specified loads no roster instead of silently defaulting to
    Bloomington. Interactive runs are prompted via _prompt_roster_choice.
    """
    import run_local
    calls = []

    def mock_load_roster(path=None, *, body_slug=None):
        calls.append({"path": path, "body_slug": body_slug})
        return object()

    with patch("src.roster.load_roster", side_effect=mock_load_roster):
        roster = run_local._resolve_roster(None, None)

    assert roster is None, "No body + no choice must resolve to no roster"
    assert calls == [], "load_roster must not be called in the no-roster default path"
```

- [ ] **Step 2: Run the updated test to verify it passes**

Run: `python -m pytest tests/test_body_tagging.py::test_no_body_no_choice_uses_no_roster -v`
Expected: PASS

- [ ] **Step 3: Wire the chooser into run_pipeline**

In `run_local.py`, find the end of the Phase 109 resolve block — the comment line:

```python
    # else: D-05 (no flag, no persisted — legacy) or D-06 (no flag, persisted — silent read)

    effective_body_slug = state.body_slug  # used by Plan 02 guard + Plan 03 Stage 4
```

Insert the chooser call between those two lines:

```python
    # else: D-05 (no flag, no persisted — legacy) or D-06 (no flag, persisted — silent read)

    # Roster chooser: on a fresh interactive run with no --body, ask which
    # roster should guide Stage 4 instead of silently using the legacy file.
    # Non-interactive runs (no TTY) fall through to no roster (handled in
    # _resolve_roster) unless --body was passed.
    if _should_prompt_roster(
        cli_body=cli_body,
        persisted_body=persisted_body,
        roster_choice=state.roster_choice,
        identified=state.is_complete(PipelineStage.IDENTIFIED),
        isatty=sys.stdin.isatty(),
    ):
        chosen_slug, marker = _prompt_roster_choice()
        state.roster_choice = marker
        if chosen_slug:
            state.body_slug = chosen_slug
        state.save()

    effective_body_slug = state.body_slug  # used by Plan 02 guard + Plan 03 Stage 4
```

(`PipelineStage` and `PipelineState` are already imported at the top of `run_pipeline`; `cli_body` and `persisted_body` are already defined earlier in the resolve block.)

- [ ] **Step 4: Verify the module imports and the full chooser suite passes**

Run: `python -c "import run_local" && python -m pytest tests/test_roster_chooser.py tests/test_body_tagging.py -v`
Expected: PASS (all chooser tests + all body-tagging tests green)

- [ ] **Step 5: Manual smoke check of the menu wording (optional but recommended)**

Run:
```bash
python -c "
import builtins, run_local
from unittest.mock import patch
from src import config
print('cached rosters:', run_local._list_cached_rosters())
with patch('builtins.input', lambda *a: ''):
    print('bare-enter result:', run_local._prompt_roster_choice())
"
```
Expected: prints the real cached roster list and `('None', '__none__')`-style result; the printed menu matches the spec (`ROSTER SELECTION` banner, numbered options, `No roster` last).

- [ ] **Step 6: Commit**

```bash
git add run_local.py tests/test_body_tagging.py
git commit -m "feat(run_local): prompt for roster when no --body on interactive runs"
```

---

## Task 7: Update README

**Files:**
- Modify: `README.md:91-99` ("Speaker identification strategy" section)

- [ ] **Step 1: Add the roster-selection note**

In `README.md`, after the existing "Speaker identification strategy" section (after the line
`Speakers below 0.70 confidence are flagged for human review via a Colab form widget.`),
insert a new subsection:

```markdown
### Choosing a roster (local CLI)

Speaker identification can be guided by a council roster (it corrects
transcription errors against known member names). When you run `run_local.py`
**interactively without `--body`**, CouncilScribe now asks which roster to use:

- any cached per-body roster under `~/CouncilScribe/config/rosters/` (added
  with `python refresh_roster.py --body <slug>`),
- the legacy `~/CouncilScribe/config/council_roster.json`, or
- **No roster** (the default — just press Enter) to skip name correction.

Picking a cached roster tags the meeting (like passing `--body <slug>`), so
resuming it reuses that roster automatically. Pass `--body <slug>` explicitly
to skip the prompt. In non-interactive runs (batch mode, piped, cron) with no
`--body`, no roster is used.
```

- [ ] **Step 2: Verify the section renders (sanity check)**

Run: `grep -n "Choosing a roster" README.md`
Expected: one match.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document roster chooser in README"
```

---

## Final verification

- [ ] **Run the full test suite**

Run: `python -m pytest -q`
Expected: all pass (new `tests/test_roster_chooser.py` + updated `tests/test_body_tagging.py` + everything else).

- [ ] **Confirm the behavior change is documented and intentional**

The non-interactive "no `--body` → no roster" flip is covered by
`test_resolve_roster_unchosen_defaults_to_no_roster` and
`test_no_body_no_choice_uses_no_roster`, and noted in the README. Any cron/batch
job that relied on the legacy Bloomington default must now pass
`--body bloomington-common-council`.

---

## Self-review notes (for the implementer)

- **Spec coverage:** D1 (Task 6 gate via `_should_prompt_roster`), D2 (Task 4 menu incl. legacy + none), D3 (Task 5 `_resolve_roster` default None + Task 6 wiring), D4 (Task 6 sets `body_slug` on cached pick). Persistence (Task 1). Stage 4 contract (Task 5). Test update (Task 6). README (Task 7).
- **Type consistency:** markers `"__legacy__"`/`"__none__"` and the `(body_slug, marker)` return shape are identical across Tasks 4, 5, 6. `_resolve_roster(effective_body_slug, roster_choice)` arg order matches every call site.
- **No new flags**; `refresh_roster.py` remains the way to add a body. Offline utilities (`--show-roster`, `--fix-profiles`, `--fix-transcripts`) intentionally untouched.
