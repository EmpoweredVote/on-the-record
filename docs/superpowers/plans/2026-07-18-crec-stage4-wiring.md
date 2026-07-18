# CREC Stage-4 Wiring (Phase 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Stage 4 (`identify_speakers`) resolve diarized labels to congressional members via the CREC alignment, triggerable from the CLI with `--congressional-record DATE CHAMBER`.

**Architecture:** New `src/crec_identify.py` bridges Phase-3 `LabelResolution`s to `SpeakerMapping`s and orchestrates Phases 1–3 (`crec_speaker_mappings`). `identify_speakers` gains a `crec_mappings` param + a CREC layer (authoritative when confident, runs before `_dedupe_identities`). `run_local.py` gains a thin flag that calls the orchestrator. Identity only — never touches timestamps/words. Essentials `politician_id` link is deferred (name + bioguide marker).

**Tech Stack:** Python 3.14 (`.venv/bin/python`), pytest, stdlib. Reuses shipped `src/govinfo.py`, `src/congress_roster.py`, `src/crec_normalize.py`, `src/crec_align.py`, `src/models.py`. No new deps.

**Spec:** `docs/superpowers/specs/2026-07-18-crec-stage4-wiring-design.md`.

**Key facts:**
- `SpeakerMapping` (`src/models.py`): `speaker_label, speaker_name, confidence, id_method, needs_review, politician_slug, politician_id, local_slug, local_role, speaker_status`.
- `identify_speakers(segments, speaker_embeddings, stored_profiles=None, llm_identify_fn=None, roster=None, profile_db=None)` (`src/identify.py:383`) runs layers, then `correct_mappings` (only if `roster`), then `_dedupe_identities`, then review-flagging.
- `LabelResolution` (`src/crec_align.py`): `speaker_label, member (Optional[CongressMember]), role, confidence, method, needs_review, matched_turns, total_turns`.

---

## File Structure

- Create: `src/crec_identify.py` — converter + CLI-arg helper + orchestration.
- Create: `tests/test_crec_identify.py` — converter, arg-helper, and orchestration tests.
- Modify: `src/identify.py` — add `crec_mappings` param + CREC layer.
- Modify: `tests/test_identification.py` — CREC-layer tests.
- Modify: `run_local.py` — argparse flag + call-site wiring (imports the helper; no new logic).

---

### Task 1: `crec_identify.py` — converter + CLI-arg helper

**Files:**
- Create: `src/crec_identify.py`
- Test: `tests/test_crec_identify.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_crec_identify.py`:

```python
# tests/test_crec_identify.py
from __future__ import annotations

import pytest

from src.congress_roster import CongressMember
from src.crec_align import LabelResolution
from src.crec_identify import label_resolution_to_mapping, parse_crec_arg


def _member():
    return CongressMember("M000355", "Mitch McConnell", "McConnell", "KY", None, "senate", "Republican")


def test_convert_confident_member():
    res = LabelResolution(speaker_label="S0", member=_member(), confidence=0.9,
                          method="congressional_record", needs_review=False,
                          matched_turns=2, total_turns=2)
    m = label_resolution_to_mapping(res)
    assert m.speaker_label == "S0"
    assert m.speaker_name == "Mitch McConnell"
    assert m.id_method == "congressional_record"
    assert m.confidence == 0.9
    assert m.local_slug == "congress-M000355"
    assert m.politician_id is None
    assert m.needs_review is False


def test_convert_role():
    res = LabelResolution(speaker_label="S9", role="presiding_officer", confidence=1.0,
                          method="congressional_record", needs_review=False,
                          matched_turns=1, total_turns=1)
    m = label_resolution_to_mapping(res)
    assert m.speaker_name == "The Presiding Officer"
    assert m.id_method == "congressional_record"
    assert m.local_slug is None
    assert m.needs_review is False


def test_convert_role_unknown_slug_titlecases():
    res = LabelResolution(speaker_label="S9", role="some_new_role", method="congressional_record")
    m = label_resolution_to_mapping(res)
    assert m.speaker_name == "Some New Role"


def test_convert_ambiguous():
    res = LabelResolution(speaker_label="S0", method="ambiguous", needs_review=True)
    m = label_resolution_to_mapping(res)
    assert m.speaker_name is None
    assert m.needs_review is True
    assert m.speaker_status == "unidentified"


def test_convert_unresolved_returns_none():
    res = LabelResolution(speaker_label="S0", method="unresolved")
    assert label_resolution_to_mapping(res) is None


def test_parse_crec_arg_valid_lowercases_chamber():
    assert parse_crec_arg(["2018-10-10", "Senate"]) == ("2018-10-10", "senate")


def test_parse_crec_arg_none_when_absent():
    assert parse_crec_arg(None) is None


def test_parse_crec_arg_bad_date():
    with pytest.raises(SystemExit):
        parse_crec_arg(["10/10/2018", "house"])


def test_parse_crec_arg_bad_chamber():
    with pytest.raises(SystemExit):
        parse_crec_arg(["2018-10-10", "congress"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_identify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.crec_identify'`.

- [ ] **Step 3: Write minimal implementation** — create `src/crec_identify.py`:

```python
# src/crec_identify.py
"""Phase 4: bridge CREC alignment to Stage-4 SpeakerMappings + CLI orchestration.

Converts Phase-3 LabelResolutions into SpeakerMappings and orchestrates Phases
1-3 (fetch -> roster -> annotate -> align -> convert) for a floor session. The
essentials politician_id link is intentionally deferred: a resolved member gets
its name + bioguide (stashed in local_slug), not a politician_id.
"""
from __future__ import annotations

import re
from typing import Optional

from .models import SpeakerMapping
from .crec_align import LabelResolution, align_crec_to_diarization

_ROLE_DISPLAY = {
    "presiding_officer": "The Presiding Officer",
    "speaker": "The Speaker",
    "president_pro_tempore": "The President pro tempore",
    "vice_president": "The Vice President",
    "chief_justice": "The Chief Justice",
    "chair": "The Chair",
    "clerk": "The Clerk",
}


def label_resolution_to_mapping(res: LabelResolution) -> Optional[SpeakerMapping]:
    """Convert a LabelResolution to a SpeakerMapping (or None if unresolved).

    Confident member -> name + `congress-<bioguide>` in local_slug (no politician_id).
    Role -> a human role display name. Ambiguous -> needs_review/unidentified.
    """
    if res.member is not None:
        return SpeakerMapping(
            speaker_label=res.speaker_label,
            speaker_name=res.member.full_name,
            confidence=res.confidence,
            id_method="congressional_record",
            needs_review=False,
            local_slug=f"congress-{res.member.bioguide}",
        )
    if res.role is not None:
        display = _ROLE_DISPLAY.get(res.role, res.role.replace("_", " ").title())
        return SpeakerMapping(
            speaker_label=res.speaker_label,
            speaker_name=display,
            confidence=res.confidence,
            id_method="congressional_record",
            needs_review=False,
        )
    if res.method == "ambiguous":
        return SpeakerMapping(
            speaker_label=res.speaker_label,
            needs_review=True,
            speaker_status="unidentified",
        )
    return None


def parse_crec_arg(value) -> Optional[tuple[str, str]]:
    """Validate a `--congressional-record DATE CHAMBER` arg.

    Returns (date, lowercased_chamber) or None when the flag is absent. Raises
    SystemExit with a clear message on a bad date or chamber.
    """
    if not value:
        return None
    date, chamber = value
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise SystemExit(
            f"--congressional-record DATE must be YYYY-MM-DD, got {date!r}")
    ch = chamber.lower()
    if ch not in ("house", "senate"):
        raise SystemExit(
            f"--congressional-record CHAMBER must be 'house' or 'senate', got {chamber!r}")
    return (date, ch)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_identify.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/crec_identify.py tests/test_crec_identify.py
git commit -m "feat(crec_identify): LabelResolution->SpeakerMapping converter + CLI arg parse"
```

---

### Task 2: `crec_identify.py` — `crec_speaker_mappings` orchestration

**Files:**
- Modify: `src/crec_identify.py`
- Test: `tests/test_crec_identify.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_crec_identify.py` (injected `fetch` routes to inline CREC granule content + the real legislators fixture; `cache_path=tmp_path` keeps the real roster cache untouched):

```python
# add to tests/test_crec_identify.py
import json
from pathlib import Path

from src.models import Segment
from src.crec_identify import crec_speaker_mappings

_LEG_FIX = Path(__file__).parent / "fixtures" / "congress" / "legislators-current.sample.json"

_GRANULES = (
    '{"count":1,"granules":['
    '{"granuleId":"CREC-2025-01-10-pt1-PgS100-1","granuleClass":"SENATE","title":"HEALTHCARE"}'
    '],"nextPage":null}'
)
_HTM = (
    "<html><body><pre>\n"
    "[Congressional Record Volume 171, Number 5 (Friday, January 10, 2025)]\n"
    "[Senate]\n"
    "[Page S100]\n"
    "From the Congressional Record Online through the Government Publishing Office "
    "[<a href=\"https://www.gpo.gov\">www.gpo.gov</a>]\n\n"
    "                   HEALTHCARE FUNDING\n\n"
    "  Mr. McCONNELL. I move to proceed to the healthcare funding bill.\n"
    "  Ms. BALDWIN of Wisconsin. I rise in strong support of the healthcare measure.\n\n"
    "                          ____________________\n\n"
    "</pre></body></html>"
)


def _fake_fetch(url: str) -> str:
    if "legislators-current" in url:
        return _LEG_FIX.read_text(encoding="utf-8")
    if "/granules/" in url and "/htm" in url:
        return _HTM
    if "/granules?" in url:
        return _GRANULES
    raise AssertionError(f"unexpected url {url}")


def _seg(i, label, text):
    return Segment(segment_id=i, start_time=float(i), end_time=float(i + 1),
                   speaker_label=label, text=text)


def test_crec_speaker_mappings_resolves_members(tmp_path):
    segs = [
        _seg(0, "SPEAKER_00", "I move to proceed to the healthcare funding bill today"),
        _seg(1, "SPEAKER_01", "I rise in strong support of this healthcare measure"),
    ]
    out = crec_speaker_mappings(
        "2025-01-10", "senate", segs,
        fetch=_fake_fetch, cache_path=tmp_path / "leg.json", min_confidence=0.4)
    assert out["SPEAKER_00"].speaker_name == "Mitch McConnell"
    assert out["SPEAKER_00"].id_method == "congressional_record"
    assert out["SPEAKER_00"].local_slug == "congress-M000355"
    assert out["SPEAKER_01"].speaker_name == "Tammy Baldwin"


def test_crec_speaker_mappings_empty_when_no_record(tmp_path):
    def no_record(url):
        raise RuntimeError("404 no CREC package")
    out = crec_speaker_mappings(
        "1900-01-01", "senate", [_seg(0, "SPEAKER_00", "hello")],
        fetch=no_record, cache_path=tmp_path / "leg.json")
    assert out == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_identify.py -k crec_speaker_mappings -v`
Expected: FAIL — `ImportError: cannot import name 'crec_speaker_mappings'`.

- [ ] **Step 3: Write minimal implementation** — append to `src/crec_identify.py`. Add these imports at the top of the file with the others:

```python
from .govinfo import fetch_congressional_record_turns
from .congress_roster import load_current_roster
from .crec_normalize import annotate_turns
```

Then append:

```python
def crec_speaker_mappings(
    date: str,
    chamber: str,
    segments,
    *,
    fetch=None,
    min_confidence: float = 0.5,
    cache_path=None,
) -> dict:
    """Resolve diarized speaker labels via the Congressional Record for a session.

    Orchestrates Phases 1-3: fetch CREC turns -> load the current-Congress roster
    -> annotate turns with identities -> align onto the diarized segments ->
    convert to SpeakerMappings. `fetch` (injectable) and `cache_path` are threaded
    to the network/cache layers for testing. Returns {} when there is no Record.
    """
    fkw = {"fetch": fetch} if fetch is not None else {}
    turns = fetch_congressional_record_turns(date, chamber, **fkw)
    if not turns:
        return {}
    roster = load_current_roster(chamber, cache_path=cache_path, **fkw)
    annotated = annotate_turns(turns, roster)
    resolutions = align_crec_to_diarization(segments, annotated, min_confidence=min_confidence)

    mappings: dict = {}
    for label, res in resolutions.items():
        m = label_resolution_to_mapping(res)
        if m is not None:
            mappings[label] = m
    return mappings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_identify.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add src/crec_identify.py tests/test_crec_identify.py
git commit -m "feat(crec_identify): crec_speaker_mappings orchestration (Phases 1-3)"
```

---

### Task 3: `identify.py` — `crec_mappings` param + CREC layer

**Files:**
- Modify: `src/identify.py`
- Test: `tests/test_identification.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_identification.py`:

```python
# add to tests/test_identification.py
from src.identify import identify_speakers
from src.models import Segment, SpeakerMapping


def _two_segments():
    return [
        Segment(segment_id=0, start_time=0.0, end_time=5.0, speaker_label="SPEAKER_00",
                text="I move to proceed to the healthcare funding bill"),
        Segment(segment_id=1, start_time=5.0, end_time=10.0, speaker_label="SPEAKER_01",
                text="I rise in strong support of the healthcare measure"),
    ]


def test_crec_layer_confident_overrides_and_sets_mapping():
    crec = {
        "SPEAKER_00": SpeakerMapping(
            speaker_label="SPEAKER_00", speaker_name="Mitch McConnell", confidence=0.9,
            id_method="congressional_record", local_slug="congress-M000355"),
    }
    out = identify_speakers(_two_segments(), {}, crec_mappings=crec)
    assert out["SPEAKER_00"].speaker_name == "Mitch McConnell"
    assert out["SPEAKER_00"].id_method == "congressional_record"
    # SPEAKER_01 had no CREC mapping and no other layer identified it -> review
    assert out["SPEAKER_01"].needs_review is True


def test_crec_layer_ambiguous_flags_review():
    crec = {
        "SPEAKER_00": SpeakerMapping(
            speaker_label="SPEAKER_00", needs_review=True, speaker_status="unidentified"),
    }
    out = identify_speakers(_two_segments(), {}, crec_mappings=crec)
    assert out["SPEAKER_00"].needs_review is True
    assert out["SPEAKER_00"].speaker_name is None


def test_crec_layer_dedupe_guards_two_labels_same_member():
    crec = {
        "SPEAKER_00": SpeakerMapping(
            speaker_label="SPEAKER_00", speaker_name="Mitch McConnell", confidence=0.9,
            id_method="congressional_record", local_slug="congress-M000355"),
        "SPEAKER_01": SpeakerMapping(
            speaker_label="SPEAKER_01", speaker_name="Mitch McConnell", confidence=0.7,
            id_method="congressional_record", local_slug="congress-M000355"),
    }
    out = identify_speakers(_two_segments(), {}, crec_mappings=crec)
    named = [lbl for lbl, m in out.items() if m.speaker_name == "Mitch McConnell"]
    assert named == ["SPEAKER_00"]   # higher-confidence label keeps the name; dedupe blanks the other
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_identification.py -k crec_layer -v`
Expected: FAIL — `TypeError: identify_speakers() got an unexpected keyword argument 'crec_mappings'`.

- [ ] **Step 3: Write implementation** — edit `src/identify.py`:

First, add the parameter to the `identify_speakers` signature (at `src/identify.py:383-389`):

```python
def identify_speakers(
    segments: list[Segment],
    speaker_embeddings: dict[str, np.ndarray],
    stored_profiles: Optional[dict[str, np.ndarray]] = None,
    llm_identify_fn=None,
    roster=None,
    profile_db=None,
    crec_mappings: Optional[dict] = None,
) -> dict[str, SpeakerMapping]:
```

Then insert the CREC layer immediately AFTER the Layer 3 (LLM) block and BEFORE the `# Roster correction` block (i.e. right before `if roster:`):

```python
    # CREC layer: the Congressional Record is authoritative for WHO spoke.
    # A confident CREC mapping overrides other layers for that label; an
    # ambiguous one is recorded only where nothing else identified the speaker.
    # Placed before _dedupe_identities so the collision guard sees it too.
    if crec_mappings:
        for label, cm in crec_mappings.items():
            if cm.speaker_name and not cm.needs_review:
                mappings[label] = cm
            elif label not in mappings:
                mappings[label] = cm
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_identification.py -k crec_layer -v`
Expected: PASS (3 passed). Then the whole file `.venv/bin/python -m pytest tests/test_identification.py -q` — confirm no regressions. Then the full suite `.venv/bin/python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/identify.py tests/test_identification.py
git commit -m "feat(identify): CREC identification layer (authoritative when confident)"
```

---

### Task 4: `run_local.py` — CLI flag + call-site wiring

**Files:**
- Modify: `run_local.py`

**Note:** the logic-bearing parts (`parse_crec_arg`, `crec_speaker_mappings`) are already implemented and tested in Tasks 1–2. This task is thin integration; verify via `--help` and an import smoke check rather than new unit tests.

- [ ] **Step 1: Add the argparse flag** — in `build_parser()` (near the other `add_argument` calls, e.g. after `--diarizer`/`--compute` around `run_local.py:3621-3630`), add:

```python
    parser.add_argument(
        "--congressional-record", nargs=2, metavar=("DATE", "CHAMBER"), default=None,
        help="Resolve speakers from the Congressional Record for a floor session: "
             "DATE (YYYY-MM-DD) and CHAMBER (house|senate).")
```

- [ ] **Step 2: Wire the call site** — in the Stage-4 block, immediately BEFORE the `mappings = identify_speakers(` call at `run_local.py:1389`, insert:

```python
        crec_mappings = None
        _crec = parse_crec_arg(getattr(args, "congressional_record", None))
        if _crec:
            from src.crec_identify import crec_speaker_mappings
            _crec_date, _crec_chamber = _crec
            crec_mappings = crec_speaker_mappings(_crec_date, _crec_chamber, segments)
            print(f"  Congressional Record: resolved {len(crec_mappings)} speaker label(s)")
```

And add `crec_mappings=crec_mappings,` to the `identify_speakers(...)` call arguments.

- [ ] **Step 3: Add the import** — near the top of `run_local.py` where other `src` helpers are imported at module scope (or add a local import if module-scope imports are avoided there), ensure `parse_crec_arg` is importable at the call site. Add:

```python
from src.crec_identify import parse_crec_arg
```

(Place it with the other top-level `from src....` imports if present; otherwise import locally inside `run_pipeline` just before first use. Verify no circular-import error on `python run_local.py --help`.)

- [ ] **Step 4: Verify**

Run: `.venv/bin/python run_local.py --help 2>&1 | grep -A2 congressional-record`
Expected: the `--congressional-record DATE CHAMBER` help text prints (confirms argparse wiring + no import error).

Run: `.venv/bin/python -c "from run_local import parse_crec_arg; print(parse_crec_arg(['2018-10-10','Senate']))"`
Expected: `('2018-10-10', 'senate')` (confirms the helper is reachable through run_local's import).

Run the full suite: `.venv/bin/python -m pytest -q` — confirm no regressions.

- [ ] **Step 5: Commit**

```bash
git add run_local.py
git commit -m "feat(run_local): --congressional-record flag wires CREC into Stage 4"
```

---

## Self-Review

**Spec coverage:**
- `label_resolution_to_mapping` (member/role/ambiguous/unresolved) — Task 1.
- `parse_crec_arg` (validation) — Task 1.
- `crec_speaker_mappings` orchestration (fetch→roster→annotate→align→convert, empty-Record → {}) — Task 2.
- `identify_speakers` `crec_mappings` param + CREC layer (override-when-confident, ambiguous-records-review, before `_dedupe_identities`) — Task 3.
- `run_local` `--congressional-record` flag + wiring — Task 4.
- Deferred per spec: essentials `politician_id` linkage; GUI inputs; Senate media spike. Not in this plan.

**Placeholder scan:** No TBD/TODO; every code and test step is complete.

**Type consistency:** `label_resolution_to_mapping(res) -> Optional[SpeakerMapping]` and `parse_crec_arg(value) -> Optional[tuple]` and `crec_speaker_mappings(date, chamber, segments, *, fetch, min_confidence, cache_path)` signatures match between definitions (Tasks 1–2) and call sites (Task 2 test, Task 4 wiring). `crec_mappings` param name is identical across `identify_speakers` (Task 3), the Task-3 tests, and the Task-4 call site. `SpeakerMapping` field usage (`speaker_name`, `id_method`, `local_slug`, `speaker_status`, `needs_review`, `confidence`) matches `src/models.py`.

**Behavioral check:** CREC layer sets `mappings[label]` only on confident override or when the label is otherwise unmapped, then flows through the existing `_dedupe_identities` (Task-3 dedupe test) and end-of-function review-flagging (an unmapped SPEAKER_01 becomes `needs_review=True`). `crec_speaker_mappings` threads `cache_path` so tests never touch the real roster cache. `run_local` gains only the tested helper call + 2 lines of wiring.
