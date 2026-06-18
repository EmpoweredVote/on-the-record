# tests/test_speaker_status.py
from __future__ import annotations
from src.models import SpeakerMapping


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
