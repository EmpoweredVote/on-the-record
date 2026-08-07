from __future__ import annotations

import json

from backfill_summary_sections import backfill, refit_sections, sections_stale


def _segment(segment_id, text="words"):
    return {
        "segment_id": segment_id,
        "start_time": float(segment_id * 10),
        "end_time": float(segment_id * 10 + 9),
        "speaker_label": "S0",
        "text": text,
    }


def _section(title, start, end, **overrides):
    sec = {
        "section_type": "discussion",
        "title": title,
        "content": f"About {title}.",
        "start_time": float(start * 10),
        "end_time": float(end * 10 + 9),
        "start_segment": start,
        "end_segment": end,
    }
    sec.update(overrides)
    return sec


def _write_meeting(mdir, *, n_segments, embedded_sections, standalone_sections,
                   summary_extra=None):
    transcript = {
        "meeting_id": mdir.name,
        "city": "X",
        "date": "2026-01-01",
        "meeting_type": "R",
        "event_kind": "council",
        "segments": [_segment(i) for i in range(n_segments)],
        "speakers": {},
        "summary": {
            "executive_summary": "embedded exec",
            "highlights": [],
            "sections": embedded_sections,
            "model": "m",
            "generated_at": "t",
        },
    }
    (mdir / "transcript_named.json").write_text(json.dumps(transcript))
    standalone = {
        "executive_summary": "standalone exec",
        "highlights": ["h1"],
        "sections": standalone_sections,
        "model": "m",
        "generated_at": "t",
    }
    standalone.update(summary_extra or {})
    (mdir / "summary.json").write_text(json.dumps(standalone))


def test_sections_stale_detects_out_of_range_boundary():
    valid_ids = {0, 1, 2}
    assert not sections_stale([_section("A", 0, 2)], valid_ids)
    assert sections_stale([_section("A", 0, 5)], valid_ids)  # end past transcript
    assert sections_stale([_section("A", 0, 2), _section("B", 3, 3)], valid_ids)


def test_refit_copies_boundaries_from_matching_embedded():
    standalone = [_section("A", 0, 7), _section("B", 8, 12)]  # stale numbering
    embedded = [
        _section("A", 0, 7, start_segment=0, end_segment=3),
        _section("B", 8, 12, start_segment=4, end_segment=5),
    ]
    # times/titles/content identical, only indices differ
    for s, e in zip(standalone, embedded):
        s["start_time"], s["end_time"] = e["start_time"], e["end_time"]
    assert refit_sections(standalone, embedded) == 2
    assert [(s["start_segment"], s["end_segment"]) for s in standalone] == [(0, 3), (4, 5)]


def test_refit_refuses_on_section_mismatch():
    standalone = [_section("A", 0, 7)]
    assert refit_sections(standalone, [_section("DIFFERENT", 0, 3)]) is None
    assert refit_sections(standalone, []) is None  # length mismatch
    assert standalone[0]["end_segment"] == 7  # untouched


def test_backfill_fixes_stale_standalone(tagged_meeting_dir, tmp_meetings_dir):
    embedded = [_section("A", 0, 1), _section("B", 2, 2)]
    stale = [
        _section("A", 0, 1, start_segment=0, end_segment=4),   # pre-merge ids
        _section("B", 2, 2, start_segment=5, end_segment=9),
    ]
    mdir = tagged_meeting_dir("x", meeting_id="2026-01-01-council")
    _write_meeting(mdir, n_segments=3, embedded_sections=embedded,
                   standalone_sections=stale, summary_extra={"custom_key": "kept"})

    stats = backfill(dry_run=False)
    assert stats["fixed"] == ["2026-01-01-council"]
    assert stats["drifted"] == [] and stats["mismatched"] == []
    assert stats["still_stale"] == []

    data = json.loads((mdir / "summary.json").read_text())
    got = [(s["start_segment"], s["end_segment"]) for s in data["sections"]]
    assert got == [(0, 1), (2, 2)]
    # everything but the boundaries is preserved
    assert data["executive_summary"] == "standalone exec"
    assert data["highlights"] == ["h1"]
    assert data["custom_key"] == "kept"


def test_backfill_leaves_valid_summary_untouched(tagged_meeting_dir, tmp_meetings_dir):
    sections = [_section("A", 0, 2)]
    mdir = tagged_meeting_dir("x", meeting_id="2026-01-01-council")
    _write_meeting(mdir, n_segments=3, embedded_sections=sections,
                   standalone_sections=sections)
    original = (mdir / "summary.json").read_text()

    stats = backfill(dry_run=False)
    assert stats["fixed"] == []
    assert stats["ok"] == 1
    assert (mdir / "summary.json").read_text() == original


def test_backfill_logs_and_skips_when_embedded_also_drifted(
        tagged_meeting_dir, tmp_meetings_dir):
    drifted = [_section("A", 0, 9)]  # out of range in BOTH copies
    mdir = tagged_meeting_dir("x", meeting_id="2026-01-01-council")
    _write_meeting(mdir, n_segments=3, embedded_sections=drifted,
                   standalone_sections=drifted)
    original = (mdir / "summary.json").read_text()

    stats = backfill(dry_run=False)
    assert stats["drifted"] == ["2026-01-01-council"]
    assert stats["fixed"] == []
    assert stats["still_stale"] == ["2026-01-01-council"]
    assert (mdir / "summary.json").read_text() == original


def test_backfill_dry_run_writes_nothing(tagged_meeting_dir, tmp_meetings_dir):
    embedded = [_section("A", 0, 2)]
    stale = [_section("A", 0, 2, start_segment=0, end_segment=9)]
    mdir = tagged_meeting_dir("x", meeting_id="2026-01-01-council")
    _write_meeting(mdir, n_segments=3, embedded_sections=embedded,
                   standalone_sections=stale)
    original = (mdir / "summary.json").read_text()

    stats = backfill(dry_run=True)
    assert stats["fixed"] == ["2026-01-01-council"]
    assert (mdir / "summary.json").read_text() == original
