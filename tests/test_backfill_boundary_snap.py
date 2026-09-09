from __future__ import annotations

import json

from src.models import Meeting, Segment, Word, SpeakerMapping
from backfill_boundary_snap import snap_meeting, backfill


def _bled_meeting(meeting_id="2026-06-23-cd1-democratic-primary-debate"):
    """Two turns with the classic '>>' leading bleed: the tail of A ('district.')
    lands at the front of B, before B's '>>' marker."""
    a = Segment(segment_id=0, start_time=490.6, end_time=597.52, speaker_label="S3")
    b = Segment(segment_id=1, start_time=597.52, end_time=609.42, speaker_label="S5")
    a.words = [Word("this", 597.38, 597.50)]
    b.words = [
        Word("district.", 597.50, 597.63),
        Word(">>", 598.18, 598.29),
        Word("Ms.", 598.29, 598.39),
        Word("Colon", 598.39, 598.50),
    ]
    for s in (a, b):
        s.text = " ".join(w.word for w in s.words)
    return Meeting(
        meeting_id=meeting_id, city="Scottsdale", date="2026-06-23",
        meeting_type="Debate", event_kind="debate", segments=[a, b],
        speakers={"S3": SpeakerMapping(speaker_label="S3"),
                  "S5": SpeakerMapping(speaker_label="S5")},
    )


def test_snap_meeting_moves_bleed_and_rebuilds_text():
    m = _bled_meeting()
    changed = snap_meeting(m)
    assert changed is True
    assert [w.word for w in m.segments[0].words] == ["this", "district."]
    assert [w.word for w in m.segments[1].words] == [">>", "Ms.", "Colon"]
    assert m.segments[0].text == "this district."        # .text rebuilt, not stale
    assert m.segments[1].text == ">> Ms. Colon"


def test_snap_meeting_reports_no_change_when_clean():
    m = _bled_meeting()
    snap_meeting(m)                                        # first pass fixes it
    assert snap_meeting(m) is False                        # idempotent: nothing left


def test_backfill_rewrites_transcript_named(tagged_meeting_dir, monkeypatch):
    mdir = tagged_meeting_dir("x", meeting_id="2026-06-23-cd1-democratic-primary-debate",
                              completed_stage=7)
    (mdir / "transcript_named.json").write_text(json.dumps(_bled_meeting().to_dict()))
    import backfill_boundary_snap as bf
    monkeypatch.setattr(bf, "live_published_slugs", lambda: None, raising=False)

    assert backfill(dry_run=False) == 1
    data = json.loads((mdir / "transcript_named.json").read_text())
    assert [w["word"] for w in data["segments"][0]["words"]] == ["this", "district."]
    assert data["segments"][1]["text"] == ">> Ms. Colon"


def test_backfill_dry_run_writes_nothing(tagged_meeting_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-06-23-cd1-democratic-primary-debate",
                              completed_stage=7)
    original = json.dumps(_bled_meeting().to_dict())
    (mdir / "transcript_named.json").write_text(original)

    assert backfill(dry_run=True) == 1                     # reports it would change
    assert (mdir / "transcript_named.json").read_text() == original  # untouched


def test_backfill_skips_clean_meeting(tagged_meeting_dir):
    m = _bled_meeting()
    snap_meeting(m)                                        # already snapped
    mdir = tagged_meeting_dir("x", meeting_id="2026-06-23-cd1-democratic-primary-debate",
                              completed_stage=7)
    (mdir / "transcript_named.json").write_text(json.dumps(m.to_dict()))
    assert backfill(dry_run=False) == 0                    # nothing to do


def test_backfill_only_touches_the_named_meetings(tagged_meeting_dir, monkeypatch):
    # A targeted re-snap: this change's fix affects 2 meetings, while 54 others
    # carry pending corrections from the earlier pass. Applying the fix must not
    # drag those unrelated meetings along.
    wanted = tagged_meeting_dir("a", meeting_id="2026-06-23-cd1-democratic-primary-debate",
                                completed_stage=7)
    other = tagged_meeting_dir("b", meeting_id="2026-06-24-cd1-republican-primary-debate",
                               completed_stage=7)
    for mdir, mid in ((wanted, "2026-06-23-cd1-democratic-primary-debate"),
                      (other, "2026-06-24-cd1-republican-primary-debate")):
        (mdir / "transcript_named.json").write_text(json.dumps(_bled_meeting(mid).to_dict()))
    untouched = (other / "transcript_named.json").read_text()
    import backfill_boundary_snap as bf
    monkeypatch.setattr(bf, "live_published_slugs", lambda: None, raising=False)

    assert backfill(dry_run=False, only=["2026-06-23-cd1-democratic-primary-debate"]) == 1

    fixed = json.loads((wanted / "transcript_named.json").read_text())
    assert [w["word"] for w in fixed["segments"][0]["words"]] == ["this", "district."]
    assert (other / "transcript_named.json").read_text() == untouched


def test_backfill_rejects_a_meeting_id_that_is_not_present(tagged_meeting_dir, monkeypatch):
    # A typo in a meeting id would otherwise report "nothing needed re-snapping"
    # and look like success, so it has to fail loudly.
    tagged_meeting_dir("a", meeting_id="2026-06-23-cd1-democratic-primary-debate",
                       completed_stage=7)
    import backfill_boundary_snap as bf
    monkeypatch.setattr(bf, "live_published_slugs", lambda: None, raising=False)

    try:
        backfill(dry_run=True, only=["no-such-meeting"])
    except ValueError as exc:
        assert "no-such-meeting" in str(exc)
    else:
        raise AssertionError("expected ValueError for an unknown meeting id")
