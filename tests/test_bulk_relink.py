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


import pytest

from src.bulk_relink import suggest_link
from src.essentials_client import EssentialsClientError


def _cand(pid, name="Cand"):
    return {"politician_id": pid, "politician_slug": None, "full_name": name,
            "office_title": "", "district_label": "", "is_incumbent": False,
            "government_name": ""}


def _speaker(name, known_id=None):
    return UnlinkedSpeaker(display_name=name, normalized_name=name.lower(), known_id=known_id)


def test_suggest_known_id_skips_search():
    calls = []

    def search(q, **kw):
        calls.append(q)
        return []

    decision, candidates = suggest_link(_speaker("Steve Hilton", known_id="uuid-h"), search=search)
    assert decision == DECISION_LINK
    assert candidates[0]["politician_id"] == "uuid-h"
    assert calls == []  # fast path: search never called


def test_suggest_single_match_links():
    decision, candidates = suggest_link(
        _speaker("Steve Hilton"), search=lambda q, **kw: [_cand("uuid-1", "Steve Hilton")])
    assert decision == DECISION_LINK
    assert candidates == [_cand("uuid-1", "Steve Hilton")]


def test_suggest_zero_matches_reviews():
    decision, candidates = suggest_link(_speaker("Nobody"), search=lambda q, **kw: [])
    assert decision == DECISION_REVIEW
    assert candidates == []


def test_suggest_multiple_matches_reviews_with_candidates():
    cands = [_cand("uuid-1"), _cand("uuid-2")]
    decision, candidates = suggest_link(_speaker("John Smith"), search=lambda q, **kw: cands)
    assert decision == DECISION_REVIEW
    assert candidates == cands


def test_suggest_propagates_api_error():
    def boom(q, **kw):
        raise EssentialsClientError("down")

    with pytest.raises(EssentialsClientError):
        suggest_link(_speaker("Steve Hilton"), search=boom)


from src.bulk_relink import BulkRelinkParseError, ReviewDecision, parse_review_doc

_UUID = "9a60d603-194d-410f-ae01-85bd6293f1a7"


def test_parse_extracts_link_review_skip():
    doc = {"speakers": [
        {"name": "Steve Hilton", "decision": "link", "politician_id": _UUID},
        {"name": "Katie Porter", "decision": "review", "politician_id": None},
        {"name": "Moderator", "decision": "skip"},
    ]}
    rows = parse_review_doc(doc)
    assert rows == [
        ReviewDecision("Steve Hilton", "link", _UUID),
        ReviewDecision("Katie Porter", "review", None),
        ReviewDecision("Moderator", "skip", None),
    ]


def test_parse_empty_or_missing_speakers_returns_empty():
    assert parse_review_doc({}) == []
    assert parse_review_doc({"speakers": []}) == []


def test_parse_rejects_unknown_decision():
    with pytest.raises(BulkRelinkParseError) as ei:
        parse_review_doc({"speakers": [{"name": "X", "decision": "approve"}]})
    assert "X" in str(ei.value)


def test_parse_rejects_link_without_uuid():
    with pytest.raises(BulkRelinkParseError) as ei:
        parse_review_doc({"speakers": [{"name": "Steve", "decision": "link", "politician_id": None}]})
    assert "Steve" in str(ei.value)


def test_parse_rejects_link_with_malformed_uuid():
    with pytest.raises(BulkRelinkParseError) as ei:
        parse_review_doc({"speakers": [{"name": "Steve", "decision": "link", "politician_id": "not-a-uuid"}]})
    assert "Steve" in str(ei.value)


def test_parse_rejects_row_without_name():
    with pytest.raises(BulkRelinkParseError):
        parse_review_doc({"speakers": [{"decision": "skip"}]})


from src.bulk_relink import build_review_doc


def _row(name, decision, candidates, **kw):
    s = UnlinkedSpeaker(display_name=name, normalized_name=name.lower(),
                        decision=decision, candidates=candidates, **kw)
    return s


def test_build_doc_link_row_has_id_and_no_candidates():
    rows = [_row("Steve Hilton", DECISION_LINK,
                 [{"politician_id": "uuid-h", "full_name": "Steve Hilton",
                   "office_title": "Candidate", "district_label": "CA"}],
                 meeting_count=4, has_voice_profile=True)]
    doc = build_review_doc(rows)
    entry = doc["speakers"][0]
    assert entry["name"] == "Steve Hilton"
    assert entry["meeting_count"] == 4
    assert entry["has_voice_profile"] is True
    assert entry["decision"] == "link"
    assert entry["politician_id"] == "uuid-h"
    assert "candidates" not in entry


def test_build_doc_review_row_has_null_id_and_candidate_hints():
    rows = [_row("Katie Porter", DECISION_REVIEW, [
        {"politician_id": "uuid-1", "full_name": "Katie Porter",
         "office_title": "U.S. Representative", "district_label": "CA-47"},
        {"politician_id": "uuid-2", "full_name": "Katie Porter",
         "office_title": "Senator", "district_label": "CA"},
    ])]
    doc = build_review_doc(rows)
    entry = doc["speakers"][0]
    assert entry["decision"] == "review"
    assert entry["politician_id"] is None
    assert entry["candidates"] == [
        {"id": "uuid-1", "name": "Katie Porter", "office": "U.S. Representative", "district": "CA-47"},
        {"id": "uuid-2", "name": "Katie Porter", "office": "Senator", "district": "CA"},
    ]


def test_build_doc_is_yaml_round_trippable():
    import yaml
    rows = [_row("Katie Porter", DECISION_REVIEW,
                 [{"politician_id": "uuid-1", "full_name": "Katie Porter",
                   "office_title": "Rep", "district_label": "CA-47"}])]
    doc = build_review_doc(rows)
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    assert yaml.safe_load(text) == doc


from src.publish import resolve_races_for_politicians


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = None
        self.params = None

    def execute(self, sql, params=None):
        self.executed = sql
        self.params = params

    def fetchall(self):
        return self._rows


def test_resolve_races_returns_all_distinct():
    cur = _FakeCursor([("race-1",), ("race-2",)])
    assert resolve_races_for_politicians(cur, ["pol-a", "pol-b"]) == ["race-1", "race-2"]


def test_resolve_races_single():
    cur = _FakeCursor([("race-1",)])
    assert resolve_races_for_politicians(cur, ["pol-a"]) == ["race-1"]


def test_resolve_races_empty_when_no_rows():
    cur = _FakeCursor([])
    assert resolve_races_for_politicians(cur, ["pol-a"]) == []


def test_resolve_races_empty_politician_list_skips_query():
    cur = _FakeCursor([("race-1",)])
    assert resolve_races_for_politicians(cur, []) == []
    assert cur.executed is None


def test_resolve_races_casts_param_to_uuid_array():
    # Regression: essentials.race_candidates.politician_id is uuid; psycopg2 sends
    # a list as text[], so the query MUST cast (ANY(%s::uuid[])).
    cur = _FakeCursor([("race-1",)])
    resolve_races_for_politicians(cur, ["11111111-1111-1111-1111-111111111111"])
    assert "::uuid[]" in cur.executed
