# tests/test_speaker_status.py
from __future__ import annotations
import numpy as np
from src.enroll import ProfileDB, enroll_speakers
from src.models import Meeting, Segment, SpeakerMapping
from src import quality
from src.review import make_unidentified_slug, mark_unidentified, mark_non_speaker
from src.enroll import resolve_mapping_enrollment


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


def test_unidentified_slug_is_unique_per_meeting_and_label():
    a = make_unidentified_slug("2026-02-04-council", "SPEAKER_07")
    b = make_unidentified_slug("2026-05-06-debate", "SPEAKER_07")
    assert a != b
    assert a == make_unidentified_slug("2026-02-04-council", "SPEAKER_07")  # deterministic
    assert a.startswith("unidentified-")


def test_resolve_keys_unidentified_by_local_slug_not_name():
    m1 = SpeakerMapping(speaker_label="S0", speaker_name="Interviewee 1",
                        local_slug="unidentified-mA-S0", speaker_status="unidentified")
    m2 = SpeakerMapping(speaker_label="S0", speaker_name="Interviewee 1",
                        local_slug="unidentified-mB-S0", speaker_status="unidentified")
    k1, s1, _ = resolve_mapping_enrollment(m1, roster=None)
    k2, s2, _ = resolve_mapping_enrollment(m2, roster=None)
    assert k1 == "local:unidentified-mA-S0"
    assert k2 == "local:unidentified-mB-S0"
    assert k1 != k2          # two "Interviewee 1"s never merge
    assert s1 is None and s2 is None


def test_resolve_prefers_politician_slug_over_local():
    m = SpeakerMapping(speaker_label="S0", speaker_name="Jane Adams",
                       politician_slug="jane-adams", politician_id="uuid",
                       local_slug="should-be-ignored")
    assert resolve_mapping_enrollment(m, roster=None) == ("essentials:jane-adams", "jane-adams", "uuid")


def test_unidentified_slug_is_bounded_and_nonempty():
    s = make_unidentified_slug("x" * 200, "SPEAKER_00")
    assert len(s) <= 100 and s.startswith("unidentified-")
    assert make_unidentified_slug("!!!", "!!!").startswith("unidentified-")  # no empty tail


def test_mark_unidentified_sets_unique_handle_and_status():
    segs = [Segment(0, 0, 5, "S0", "hi")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="City Council District 3")}
    mark_unidentified(mappings, segs, "S0", "2026-02-04-council", display_label="Unknown Commenter")
    m = mappings["S0"]
    assert m.speaker_status == "unidentified"
    assert m.local_slug == "unidentified-2026-02-04-council-s0"
    assert m.speaker_name == "Unknown Commenter"
    assert m.politician_slug is None
    assert m.id_method == "human_review" and m.confidence == 1.0
    assert segs[0].speaker_name == "Unknown Commenter"


def test_mark_unidentified_defaults_label():
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    mark_unidentified(mappings, [], "S0", "m1", display_label=None)
    assert mappings["S0"].speaker_name == "Unidentified Speaker"


def test_mark_non_speaker_clears_identity_and_sets_status():
    segs = [Segment(0, 0, 5, "S0", "hi", speaker_name="Mayor Smith")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Mayor Smith",
                                     politician_slug="stale", local_slug="stale")}
    mark_non_speaker(mappings, segs, "S0", display_label="Outro Music")
    m = mappings["S0"]
    assert m.speaker_status == "non_speaker"
    assert m.politician_slug is None and m.local_slug is None
    assert m.id_method == "human_review"
    assert m.speaker_name == "Outro Music"
    assert segs[0].speaker_name == "Outro Music"   # stale wrong name overwritten


def test_mark_non_speaker_defaults_label():
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Bad Guess")}
    mark_non_speaker(mappings, [], "S0", display_label=None)
    assert mappings["S0"].speaker_name == "Non-speaker"
