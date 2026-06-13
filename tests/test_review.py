"""Tests for the pure speaker-review core (spec 2026-06-08-unified-cli-review)."""
from __future__ import annotations

import numpy as np
import pytest

from src.models import Segment, SpeakerMapping
from src import review


def _seg(label, start, end, text=""):
    return Segment(segment_id=0, start_time=start, end_time=end, speaker_label=label, text=text)


class _FakeProfile:
    def __init__(self, display_name, centroid):
        self.display_name = display_name
        self.centroid = centroid
        self.embeddings = [centroid]


class _FakeProfileDB:
    def __init__(self, profiles):
        self.profiles = profiles  # id -> _FakeProfile


def test_build_review_state_orders_by_speech_desc():
    segments = [
        _seg("SPEAKER_00", 0, 5, "hello"),
        _seg("SPEAKER_01", 5, 35, "a much longer turn"),
    ]
    mappings = {
        "SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00"),
        "SPEAKER_01": SpeakerMapping(speaker_label="SPEAKER_01"),
    }
    views = review.build_review_state(segments, mappings, {}, _FakeProfileDB({}), show_text=True)
    assert [v.label for v in views] == ["SPEAKER_01", "SPEAKER_00"]
    assert views[0].total_speech_seconds == 30.0
    assert views[0].seg_count == 1


def test_build_review_state_show_text_toggle():
    segments = [_seg("SPEAKER_00", 0, 5, "hello there")]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}
    with_text = review.build_review_state(segments, mappings, {}, _FakeProfileDB({}), show_text=True)
    no_text = review.build_review_state(segments, mappings, {}, _FakeProfileDB({}), show_text=False)
    assert with_text[0].sample_text == "hello there"
    assert no_text[0].sample_text is None
    assert with_text[0].clip_start == 0.0
    assert no_text[0].clip_start == 0.0


def test_build_review_state_includes_soft_hints():
    vec = np.array([1.0, 0.0, 0.0])
    segments = [_seg("SPEAKER_00", 0, 10)]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}
    embeddings = {"SPEAKER_00": vec}
    db = _FakeProfileDB({"mayor-jones": _FakeProfile("Mayor Jones", vec)})
    views = review.build_review_state(segments, mappings, embeddings, db, show_text=False)
    assert views[0].soft_hints, "expected a voice hint for an identical embedding"
    assert views[0].soft_hints[0][0] == "Mayor Jones"
    assert views[0].soft_hints[0][1] == pytest.approx(1.0, abs=1e-6)


def test_build_review_state_needs_review_flag():
    segments = [_seg("SPEAKER_00", 0, 10), _seg("SPEAKER_01", 10, 20)]
    m = SpeakerMapping(speaker_label="SPEAKER_00")
    m.needs_review = True
    mappings = {"SPEAKER_00": m, "SPEAKER_01": SpeakerMapping(speaker_label="SPEAKER_01")}
    views = review.build_review_state(segments, mappings, {}, _FakeProfileDB({}), show_text=False)
    by_label = {v.label: v for v in views}
    assert by_label["SPEAKER_00"].needs_review is True
    assert by_label["SPEAKER_01"].needs_review is False


def test_rename_speaker_updates_mapping_and_segments():
    segments = [_seg("SPEAKER_00", 0, 5, "hi"), _seg("SPEAKER_00", 6, 9, "again"), _seg("SPEAKER_01", 9, 12)]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}
    res = review.rename_speaker(mappings, segments, "SPEAKER_00", "Mayor Jones")
    assert res.new_name == "Mayor Jones"
    assert res.old_name is None
    assert mappings["SPEAKER_00"].speaker_name == "Mayor Jones"
    assert mappings["SPEAKER_00"].confidence == 1.0
    assert mappings["SPEAKER_00"].id_method == "human_review"
    assert mappings["SPEAKER_00"].needs_review is False
    assert [s.speaker_name for s in segments if s.speaker_label == "SPEAKER_00"] == ["Mayor Jones", "Mayor Jones"]


def test_rename_speaker_suggests_alias_when_correcting():
    segments = [_seg("SPEAKER_00", 0, 5)]
    m = SpeakerMapping(speaker_label="SPEAKER_00")
    m.speaker_name = "Misheard Name"
    mappings = {"SPEAKER_00": m}
    res = review.rename_speaker(mappings, segments, "SPEAKER_00", "Mayor Jones")
    assert res.old_name == "Misheard Name"
    assert res.alias_suggestion == "Misheard Name"


def test_rename_speaker_no_alias_when_no_prior_name():
    segments = [_seg("SPEAKER_00", 0, 5)]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}
    res = review.rename_speaker(mappings, segments, "SPEAKER_00", "Mayor Jones")
    assert res.alias_suggestion is None


def test_merge_speakers_full_merge():
    segments = [
        _seg("SPEAKER_00", 0, 10, "a"),   # target, 10s, 1 seg
        _seg("SPEAKER_01", 10, 40, "b"),  # source, 30s, 1 seg
    ]
    target_vec = np.array([1.0, 0.0])
    source_vec = np.array([0.0, 1.0])
    embeddings = {"SPEAKER_00": target_vec.copy(), "SPEAKER_01": source_vec.copy()}
    m0 = SpeakerMapping(speaker_label="SPEAKER_00"); m0.speaker_name = "Mayor"
    m1 = SpeakerMapping(speaker_label="SPEAKER_01")
    mappings = {"SPEAKER_00": m0, "SPEAKER_01": m1}

    res = review.merge_speakers(segments, embeddings, mappings, "SPEAKER_01", "SPEAKER_00")

    assert all(s.speaker_label == "SPEAKER_00" for s in segments)
    assert res.moved_segments == 1
    assert res.combined_name == "Mayor"
    assert "SPEAKER_01" not in embeddings
    assert "SPEAKER_01" not in mappings
    expected = (10 * target_vec + 30 * source_vec) / 40
    assert np.allclose(embeddings["SPEAKER_00"], expected)
    # relabeled + target segments all carry the merged name
    assert all(s.speaker_name == "Mayor" for s in segments)


def test_merge_adopts_source_name_when_target_unnamed():
    segments = [_seg("SPEAKER_00", 0, 10), _seg("SPEAKER_01", 10, 20)]
    embeddings = {"SPEAKER_00": np.array([1.0]), "SPEAKER_01": np.array([1.0])}
    m0 = SpeakerMapping(speaker_label="SPEAKER_00")  # unnamed target
    m1 = SpeakerMapping(speaker_label="SPEAKER_01"); m1.speaker_name = "Clerk Smith"; m1.confidence = 1.0
    mappings = {"SPEAKER_00": m0, "SPEAKER_01": m1}
    res = review.merge_speakers(segments, embeddings, mappings, "SPEAKER_01", "SPEAKER_00")
    assert res.combined_name == "Clerk Smith"
    assert mappings["SPEAKER_00"].speaker_name == "Clerk Smith"
    assert all(s.speaker_name == "Clerk Smith" for s in segments)


def test_merge_rejects_same_label():
    segments = [_seg("SPEAKER_00", 0, 10)]
    with pytest.raises(ValueError):
        review.merge_speakers(segments, {}, {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}, "SPEAKER_00", "SPEAKER_00")


def test_merge_missing_embeddings_still_relabels():
    segments = [_seg("SPEAKER_00", 0, 10), _seg("SPEAKER_01", 10, 20)]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00"),
                "SPEAKER_01": SpeakerMapping(speaker_label="SPEAKER_01")}
    res = review.merge_speakers(segments, {}, mappings, "SPEAKER_01", "SPEAKER_00")
    assert all(s.speaker_label == "SPEAKER_00" for s in segments)
    assert res.moved_segments == 1
    assert "SPEAKER_01" not in mappings


def test_speakers_needing_review():
    a = SpeakerMapping(speaker_label="A"); a.needs_review = True
    b = SpeakerMapping(speaker_label="B"); b.needs_review = False
    assert review.speakers_needing_review({"A": a, "B": b}) == ["A"]


def test_merge_carries_source_embedding_when_target_missing():
    # Target has no embedding, source does → merged target should keep source's.
    segments = [_seg("SPEAKER_00", 0, 10), _seg("SPEAKER_01", 10, 20)]
    source_vec = np.array([0.0, 1.0])
    embeddings = {"SPEAKER_01": source_vec.copy()}  # only source present
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00"),
                "SPEAKER_01": SpeakerMapping(speaker_label="SPEAKER_01")}
    review.merge_speakers(segments, embeddings, mappings, "SPEAKER_01", "SPEAKER_00")
    assert "SPEAKER_01" not in embeddings
    assert "SPEAKER_00" in embeddings
    assert np.allclose(embeddings["SPEAKER_00"], source_vec)


def test_build_review_state_clip_candidates_longest_first():
    segs = [
        _seg("S0", 0, 5, "short a"),
        _seg("S0", 5, 35, "the long identifying turn"),
        _seg("S0", 40, 42, "short b"),
    ]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    views = review.build_review_state(segs, mappings, {}, _FakeProfileDB({}), show_text=True)
    v = views[0]
    # candidates ordered by segment duration desc: 30s@5, 5s@0, 2s@40
    assert v.clip_candidates == [5.0, 0.0, 40.0]
    # default clip is the longest turn
    assert v.clip_start == 5.0
    # sample text comes from the longest turn
    assert v.sample_text == "the long identifying turn"


def test_build_review_state_clip_candidates_capped_at_8():
    segs = [_seg("S0", i * 10, i * 10 + (i + 1), "x") for i in range(12)]  # 12 segments, increasing length
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    views = review.build_review_state(segs, mappings, {}, _FakeProfileDB({}), show_text=False)
    assert len(views[0].clip_candidates) == 8  # top 8 only


def test_build_review_state_single_segment_clip_start():
    segs = [_seg("S0", 3.0, 9.0, "hello")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    views = review.build_review_state(segs, mappings, {}, _FakeProfileDB({}), show_text=True)
    assert views[0].clip_candidates == [3.0]
    assert views[0].clip_start == 3.0


def test_build_review_state_long_turn_gets_in_turn_candidates():
    # A single 200s monologue used to yield ONE candidate (the same 40s clip
    # forever). It now also offers starts every 60s while >=30s remains.
    segs = [_seg("S0", 100.0, 300.0, "long monologue")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    views = review.build_review_state(segs, mappings, {}, _FakeProfileDB({}), show_text=True)
    assert views[0].clip_candidates == [100.0, 160.0, 220.0]
    assert views[0].clip_start == 100.0


def test_build_review_state_in_turn_candidates_respect_cap():
    # One very long turn cannot crowd past the cap of 8 candidates.
    segs = [_seg("S0", 0.0, 3600.0, "hour-long item")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    views = review.build_review_state(segs, mappings, {}, _FakeProfileDB({}), show_text=False)
    assert views[0].clip_candidates == [0.0, 60.0, 120.0, 180.0, 240.0, 300.0, 360.0, 420.0]


def test_build_review_state_mixed_turns_keep_longest_first_order():
    # In-turn extras of the longest turn come before the next segment's start.
    segs = [_seg("S0", 0.0, 90.0, "long"), _seg("S0", 100.0, 110.0, "short")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    views = review.build_review_state(segs, mappings, {}, _FakeProfileDB({}), show_text=False)
    assert views[0].clip_candidates == [0.0, 60.0, 100.0]
