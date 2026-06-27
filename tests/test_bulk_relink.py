from __future__ import annotations

from src.bulk_relink import (
    DECISION_LINK,
    DECISION_REVIEW,
    DECISION_SKIP,
    UnlinkedSpeaker,
)


def test_decision_constants_have_expected_string_values():
    assert DECISION_LINK == "link"
    assert DECISION_REVIEW == "review"
    assert DECISION_SKIP == "skip"


def test_unlinked_speaker_defaults():
    s = UnlinkedSpeaker(display_name="Steve Hilton", normalized_name="steve hilton")
    assert s.appearances == []
    assert s.meeting_count == 0
    assert s.has_voice_profile is False
    assert s.known_id is None
    assert s.decision == DECISION_REVIEW
    assert s.candidates == []


import numpy as np

from src.bulk_relink import enumerate_unlinked
from src.enroll import EmbeddingRecord, ProfileDB, StoredProfile
from src.models import Meeting, SpeakerMapping


def _meeting(mid, speakers):
    return Meeting(meeting_id=mid, city="X", date="2026-04-01", speakers=speakers)


def _unlinked(label, name, status=None, local_slug=None):
    return SpeakerMapping(speaker_label=label, speaker_name=name,
                          speaker_status=status, local_slug=local_slug)


def _linked(label, name, pid):
    return SpeakerMapping(speaker_label=label, speaker_name=name, politician_id=pid)


def _profile_db(*name_slugs):
    profiles = {
        slug: StoredProfile(
            speaker_id=slug, display_name=slug,
            embeddings=[EmbeddingRecord(np.array([1.0]), "m", 1)],
        )
        for slug in name_slugs
    }
    return ProfileDB(profiles=profiles)


def test_enumerate_groups_by_name_and_counts_meetings():
    meetings = [
        _meeting("m1", {"S0": _unlinked("S0", "Katie Porter")}),
        _meeting("m2", {"S0": _unlinked("S0", "katie porter"), "S1": _unlinked("S1", "Tom Steyer")}),
    ]
    rows = enumerate_unlinked(meetings, ProfileDB(profiles={}))
    by_name = {r.normalized_name: r for r in rows}
    assert set(by_name) == {"katie porter", "tom steyer"}
    porter = by_name["katie porter"]
    assert porter.meeting_count == 2
    assert sorted(porter.appearances) == [("m1", "S0"), ("m2", "S0")]


def test_enumerate_excludes_linked_unidentified_nonspeaker_and_local():
    meetings = [_meeting("m1", {
        "S0": _linked("S0", "Already Linked", "uuid-x"),
        "S1": _unlinked("S1", "Ghost", status="unidentified"),
        "S2": _unlinked("S2", "Applause", status="non_speaker"),
        "S3": _unlinked("S3", "Local Person", local_slug="local-person"),
        "S4": _unlinked("S4", "Real Candidate"),
    })]
    rows = enumerate_unlinked(meetings, ProfileDB(profiles={}))
    assert [r.normalized_name for r in rows] == ["real candidate"]


def test_enumerate_sets_has_voice_profile_from_name_slug():
    from src.enroll import _name_to_slug
    meetings = [_meeting("m1", {"S0": _unlinked("S0", "Steve Hilton")})]
    db = _profile_db(_name_to_slug("Steve Hilton"))
    rows = enumerate_unlinked(meetings, db)
    assert rows[0].has_voice_profile is True


def test_enumerate_known_id_from_linked_appearance_elsewhere():
    # Steve linked in his interview (m1), unlinked in a debate (m2).
    meetings = [
        _meeting("m1", {"S0": _linked("S0", "Steve Hilton", "uuid-hilton")}),
        _meeting("m2", {"S0": _unlinked("S0", "Steve Hilton")}),
    ]
    rows = enumerate_unlinked(meetings, ProfileDB(profiles={}))
    assert len(rows) == 1
    assert rows[0].known_id == "uuid-hilton"


def test_enumerate_known_id_none_when_conflicting_ids():
    meetings = [
        _meeting("m1", {"S0": _linked("S0", "Jane Roe", "uuid-a")}),
        _meeting("m2", {"S0": _linked("S0", "Jane Roe", "uuid-b")}),
        _meeting("m3", {"S0": _unlinked("S0", "Jane Roe")}),
    ]
    rows = enumerate_unlinked(meetings, ProfileDB(profiles={}))
    assert rows[0].known_id is None
