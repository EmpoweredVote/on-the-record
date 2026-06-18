# tests/test_speaker_status.py
from __future__ import annotations
import numpy as np
from src.enroll import ProfileDB, enroll_speakers
from src.models import Meeting, Segment, SpeakerMapping
from src import quality


def test_speaker_status_defaults_none_and_round_trips():
    m = SpeakerMapping(speaker_label="S0", speaker_name="X")
    assert m.speaker_status is None
    assert "speaker_status" not in m.to_dict()  # omitted when None

    m2 = SpeakerMapping(speaker_label="S1", speaker_name="Music", speaker_status="non_speaker")
    d = m2.to_dict()
    assert d["speaker_status"] == "non_speaker"
    assert SpeakerMapping.from_dict(d).speaker_status == "non_speaker"


def test_from_dict_without_status_is_none():
    m = SpeakerMapping.from_dict({"speaker_label": "S0", "speaker_name": "X"})
    assert m.speaker_status is None


def _seg(label):
    return Segment(segment_id=0, start_time=0.0, end_time=30.0, speaker_label=label, text="hi")


def test_non_speaker_is_not_enrolled():
    emb = {"S0": np.array([1.0, 0.0, 0.0]), "S1": np.array([0.0, 1.0, 0.0])}
    mappings = {
        "S0": SpeakerMapping(speaker_label="S0", speaker_name="Real Person",
                             confidence=1.0, id_method="human_review"),
        "S1": SpeakerMapping(speaker_label="S1", speaker_name="Outro Music",
                             confidence=1.0, id_method="human_review",
                             speaker_status="non_speaker"),
    }
    segs = [_seg("S0"), _seg("S1")]
    db = enroll_speakers(ProfileDB(), emb, mappings, "m1", segs, roster=None)
    keys = list(db.profiles.keys())
    from src.enroll import _name_to_slug
    assert _name_to_slug("Real Person") in keys
    assert not any("music" in k.lower() for k in keys)  # non-speaker excluded


def test_enroll_mapping_skips_non_speaker_directly():
    import numpy as np
    from src.enroll import ProfileDB, _enroll_mapping
    from src.models import SpeakerMapping
    db = ProfileDB()
    m = SpeakerMapping(speaker_label="S0", speaker_name="Pledge", confidence=1.0,
                       id_method="human_review", speaker_status="non_speaker")
    _enroll_mapping(db, m, np.array([1.0, 0.0, 0.0]), "m1", 3, roster=None)
    assert db.profiles == {}


def test_non_speaker_excluded_from_gate_eligibility():
    segs = [Segment(0, 0, 120, "S0", "x", speaker_name="Real"),
            Segment(1, 120, 240, "S1", "x", speaker_name="Outro Music")]
    speakers = {
        "S0": SpeakerMapping("S0", "Real", 1.0, "human_review"),
        "S1": SpeakerMapping("S1", "Outro Music", 1.0, "human_review",
                             speaker_status="non_speaker"),
    }
    m = Meeting(meeting_id="m", city="C", date="2026-01-01",
                event_kind="council", segments=segs, speakers=speakers)
    rep = quality.evaluate_meeting(m)
    # S1's 120s must not count toward eligible speech.
    assert rep["eligible_speech_seconds"] == 120.0
    per = {p["label"]: p for p in rep["per_speaker"]}
    assert per["S1"]["eligible"] is False
