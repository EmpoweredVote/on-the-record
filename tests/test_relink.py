from __future__ import annotations

from src.models import Meeting, SpeakerMapping
from src.relink import relink_in_meeting


def _meeting(speakers: dict[str, SpeakerMapping]) -> Meeting:
    return Meeting(meeting_id="m1", city="Bloomington", date="2026-04-01", speakers=speakers)


def test_relink_matches_by_name_case_insensitive_and_sets_both_fields():
    m = _meeting({"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="steve hilton")})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert changed == ["SPEAKER_00"]
    assert m.speakers["SPEAKER_00"].politician_id == "uuid-hilton"
    assert m.speakers["SPEAKER_00"].politician_slug == "steve-hilton"


def test_relink_sets_id_when_slug_is_none():
    m = _meeting({"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Steve Hilton")})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", None)
    assert changed == ["SPEAKER_00"]
    assert m.speakers["SPEAKER_00"].politician_id == "uuid-hilton"
    assert m.speakers["SPEAKER_00"].politician_slug is None


def test_relink_no_match_returns_empty_and_leaves_mappings_untouched():
    m = _meeting({"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Jane Doe")})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert changed == []
    assert m.speakers["SPEAKER_00"].politician_id is None


def test_relink_already_linked_is_noop():
    m = _meeting({"SPEAKER_00": SpeakerMapping(
        speaker_label="SPEAKER_00", speaker_name="Steve Hilton",
        politician_id="uuid-hilton", politician_slug="steve-hilton",
    )})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert changed == []


def test_relink_matches_multiple_labels_for_same_person():
    m = _meeting({
        "SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Steve Hilton"),
        "SPEAKER_03": SpeakerMapping(speaker_label="SPEAKER_03", speaker_name="Steve Hilton"),
    })
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert sorted(changed) == ["SPEAKER_00", "SPEAKER_03"]


import pytest

from src.relink import RelinkAmbiguous, ResolvedTarget, resolve_link_target


def _cand(pid, slug, name):
    return {"politician_id": pid, "politician_slug": slug, "full_name": name,
            "office_title": "Candidate", "district_label": "", "is_incumbent": False,
            "government_name": ""}


def test_relink_corrects_an_existing_different_link():
    # Folded in from R1 review: relinking a mapping already linked to a DIFFERENT
    # politician must update both fields and report the change.
    m = _meeting({"SPEAKER_00": SpeakerMapping(
        speaker_label="SPEAKER_00", speaker_name="Steve Hilton",
        politician_id="uuid-old", politician_slug="old-slug",
    )})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert changed == ["SPEAKER_00"]
    assert m.speakers["SPEAKER_00"].politician_id == "uuid-hilton"
    assert m.speakers["SPEAKER_00"].politician_slug == "steve-hilton"


def test_resolve_single_match(monkeypatch):
    monkeypatch.setattr("src.relink.search_politicians",
                        lambda q, **kw: [_cand("uuid-1", "steve-hilton", "Steve Hilton")])
    t = resolve_link_target("Steve Hilton")
    assert t == ResolvedTarget("uuid-1", "steve-hilton", "Steve Hilton")


def test_resolve_zero_matches_raises(monkeypatch):
    monkeypatch.setattr("src.relink.search_politicians", lambda q, **kw: [])
    with pytest.raises(RelinkAmbiguous) as ei:
        resolve_link_target("Nobody Here")
    assert ei.value.candidates == []


def test_resolve_multiple_matches_raises_with_candidates(monkeypatch):
    cands = [_cand("uuid-1", "a", "John Smith"), _cand("uuid-2", "b", "John Smith")]
    monkeypatch.setattr("src.relink.search_politicians", lambda q, **kw: cands)
    with pytest.raises(RelinkAmbiguous) as ei:
        resolve_link_target("John Smith")
    assert len(ei.value.candidates) == 2


def test_resolve_explicit_id_uses_search_record_for_display(monkeypatch):
    monkeypatch.setattr("src.relink.search_politicians",
                        lambda q, **kw: [_cand("uuid-1", "a", "Other"),
                                         _cand("uuid-2", "steve-hilton", "Steve Hilton")])
    t = resolve_link_target("Steve Hilton", explicit_id="uuid-2")
    assert t == ResolvedTarget("uuid-2", "steve-hilton", "Steve Hilton")


def test_resolve_explicit_id_tolerates_no_search_hit(monkeypatch):
    monkeypatch.setattr("src.relink.search_politicians", lambda q, **kw: [])
    t = resolve_link_target("Steve Hilton", explicit_id="uuid-9")
    assert t == ResolvedTarget("uuid-9", None, "Steve Hilton")


def test_resolve_propagates_api_error_on_name_path(monkeypatch):
    from src.essentials_client import EssentialsClientError

    def boom(q, **kw):
        raise EssentialsClientError("essentials down")

    monkeypatch.setattr("src.relink.search_politicians", boom)
    with pytest.raises(EssentialsClientError):
        resolve_link_target("Steve Hilton")


def test_resolve_explicit_id_tolerates_api_error(monkeypatch):
    from src.essentials_client import EssentialsClientError

    def boom(q, **kw):
        raise EssentialsClientError("essentials down")

    monkeypatch.setattr("src.relink.search_politicians", boom)
    t = resolve_link_target("Steve Hilton", explicit_id="uuid-9")
    assert t == ResolvedTarget("uuid-9", None, "Steve Hilton")


import numpy as np

from src.enroll import EmbeddingRecord, ProfileDB, StoredProfile
from src.relink import rekey_profile_for_link


def _profile(key, name, pid=None, slug=None):
    return StoredProfile(
        speaker_id=key, display_name=name,
        embeddings=[EmbeddingRecord(np.array([1.0, 2.0]), "m1", 3)],
        meetings_seen=["m1"], total_segments_confirmed=3,
        politician_slug=slug, politician_id=pid,
    )


def test_rekey_folds_name_keyed_profile_into_essentials_id():
    db = ProfileDB(profiles={"hilton_steve": _profile("hilton_steve", "Steve Hilton")})
    key = rekey_profile_for_link(db, "Steve Hilton",
                                 politician_id="uuid-hilton", politician_slug="steve-hilton",
                                 full_name="Steve Hilton")
    assert key == "essentials:uuid-hilton"
    assert "hilton_steve" not in db.profiles
    target = db.profiles["essentials:uuid-hilton"]
    assert target.politician_id == "uuid-hilton"
    assert target.politician_slug == "steve-hilton"
    assert len(target.embeddings) == 1  # embeddings carried over


def test_rekey_returns_none_when_no_source_profile():
    db = ProfileDB(profiles={})
    key = rekey_profile_for_link(db, "Steve Hilton",
                                 politician_id="uuid-hilton", politician_slug=None,
                                 full_name="Steve Hilton")
    assert key is None
    assert db.profiles == {}


def test_rekey_noop_when_already_essentials_keyed():
    db = ProfileDB(profiles={
        "essentials:uuid-hilton": _profile("essentials:uuid-hilton", "Steve Hilton",
                                           pid="uuid-hilton", slug="steve-hilton"),
    })
    key = rekey_profile_for_link(db, "Steve Hilton",
                                 politician_id="uuid-hilton", politician_slug="steve-hilton",
                                 full_name="Steve Hilton")
    assert key == "essentials:uuid-hilton"
    assert len(db.profiles) == 1


def test_rekey_folds_all_profiles_sharing_the_politician_id():
    db = ProfileDB(profiles={
        "hilton_steve": _profile("hilton_steve", "Steve Hilton"),
        "essentials:old-slug": _profile("essentials:old-slug", "Steve Hilton",
                                        pid="uuid-hilton", slug="old-slug"),
    })
    key = rekey_profile_for_link(db, "Steve Hilton",
                                 politician_id="uuid-hilton", politician_slug="steve-hilton",
                                 full_name="Steve Hilton")
    assert key == "essentials:uuid-hilton"
    assert "hilton_steve" not in db.profiles
    assert "essentials:old-slug" not in db.profiles
    assert len(db.profiles["essentials:uuid-hilton"].embeddings) == 2  # both folded
