"""retranscribe_meeting.py: loop detection and the text-only merge."""
from __future__ import annotations

import pytest

import retranscribe_meeting as rt
from src.models import Segment, Word


def test_repetition_runs_finds_whisper_loop():
    text = "it was a big, big, big, big, big, big, big switch"
    assert rt.repetition_runs(text) == [("big,", 7)]


def test_repetition_runs_ignores_normal_speech():
    assert rt.repetition_runs("thank you thank you so much for having me") == []


def test_repetition_runs_finds_multiword_loop():
    text = "Thank you. " * 6 + "Next speaker."
    assert rt.repetition_runs(text) == [("Thank you.", 6)]


def _seg(i, a, b, label, name=None):
    s = Segment(i, a, b, label)
    s.speaker_name = name
    return s


def test_apply_new_text_keeps_times_labels_and_names():
    segs = [_seg(0, 0.0, 2.0, "SPEAKER_00", "Ann"), _seg(1, 2.0, 4.0, "SPEAKER_01", "Bo")]
    fresh = [
        {**_seg(0, 0.0, 1.9, "X").to_dict(), "text": "Hello there.",
         "words": [Word("Hello", 0.1, 0.5).to_dict(), Word("there.", 0.5, 0.9).to_dict()]},
        {**_seg(1, 2.1, 4.0, "Y").to_dict(), "text": "Hi.",
         "words": [Word("Hi.", 2.2, 2.5).to_dict()]},
    ]
    rt.apply_new_text(segs, fresh)
    assert [s.text for s in segs] == ["Hello there.", "Hi."]
    assert [(s.start_time, s.end_time, s.speaker_label, s.speaker_name) for s in segs] == [
        (0.0, 2.0, "SPEAKER_00", "Ann"), (2.0, 4.0, "SPEAKER_01", "Bo")]


def test_apply_new_text_rejects_mismatched_ids():
    segs = [_seg(0, 0.0, 2.0, "A"), _seg(1, 2.0, 4.0, "B")]
    with pytest.raises(ValueError):
        rt.apply_new_text(segs, [_seg(0, 0.0, 2.0, "A").to_dict()])


def test_reassign_raw_moves_words_onto_premerge_turns():
    named = [_seg(0, 0.0, 4.0, "A")]
    named[0].words = [Word("one", 0.2, 0.6), Word("two", 2.4, 2.8)]
    raw = [_seg(0, 0.0, 2.0, "A"), _seg(1, 2.0, 4.0, "A")]
    rt.reassign_raw(raw, named)
    assert [s.text for s in raw] == ["one", "two"]


def test_apply_promotes_reviewed_dry_run_without_calling_modal(tmp_path, monkeypatch):
    import json

    from src import config, modal_compute
    from src.models import Meeting

    mdir = tmp_path / "m1"
    mdir.mkdir()
    seg = _seg(0, 0.0, 2.0, "SPEAKER_00", "Ann")
    seg.text = "big, big, big, big, big, big"
    meeting = Meeting(meeting_id="m1", city="C", date="2026-01-01",
                      meeting_type="podcast", segments=[seg])
    (mdir / "transcript_named.json").write_text(json.dumps(meeting.to_dict()))
    (mdir / "transcript_raw.json").write_text(json.dumps([seg.to_dict()]))
    fresh = _seg(0, 0.0, 2.0, "SPEAKER_00", "Ann")
    fresh.text, fresh.words = "A big switch.", [Word("A", 0.1, 0.2), Word("big", 0.2, 0.4),
                                                 Word("switch.", 0.4, 0.8)]
    dry = Meeting(meeting_id="m1", city="C", date="2026-01-01",
                  meeting_type="podcast", segments=[fresh])
    (mdir / "transcript_named.retranscribed.json").write_text(json.dumps(dry.to_dict()))

    monkeypatch.setattr(config, "MEETINGS_DIR", tmp_path)
    def _no_modal(*a, **k):
        raise AssertionError("Modal must not run when a reviewed dry run exists")
    monkeypatch.setattr(modal_compute, "run_transcription", _no_modal)
    monkeypatch.setattr(modal_compute, "upload_audio", _no_modal)
    import src.export as export
    monkeypatch.setattr(export, "export_all", lambda *a, **k: None)

    rt.retranscribe("m1", apply=True)

    out = json.loads((mdir / "transcript_named.json").read_text())
    assert out["segments"][0]["text"] == "A big switch."
    assert out["segments"][0]["speaker_name"] == "Ann"
    assert (mdir / "backup-pre-retranscribe" / "transcript_named.json").exists()
    assert not (mdir / "transcript_named.retranscribed.json").exists()


def test_empty_turns_counts_turns_without_text():
    segs = [_seg(0, 0.0, 1.0, "A"), _seg(1, 1.0, 2.0, "B"), _seg(2, 2.0, 3.0, "A")]
    segs[0].text, segs[1].text, segs[2].text = "Hi.", "  ", ""
    assert rt.empty_turns(segs) == 2
