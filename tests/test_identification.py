"""Tests for CSIDENT-01 through CSIDENT-04: live roster drives identification.

Covers:
- SpeakerMapping politician_slug/politician_id fields (CSIDENT-04)
- roster_names_for_prompt with district labels (CSIDENT-03)
- Pattern matcher roster-gated surname rejection (CSIDENT-02)
- correct_mappings populates politician identity (CSIDENT-04)
- identify_speakers phantom elimination integration (CSIDENT-01/02)
"""

from __future__ import annotations

from src.models import Segment, SpeakerMapping, Word
from src.roster import (
    Roster,
    RosterMember,
    correct_mappings,
    correct_speaker_name,
    roster_names_for_prompt,
)
from src.identify import (
    apply_pattern_matching,
    identify_speakers,
    merge_adjacent_segments,
)


def _make_roster():
    """Build a test roster with three members including district labels."""
    return Roster(
        city="Bloomington",
        body="bloomington-common-council",
        members=[
            RosterMember(
                name="Councilmember Piedmont-Smith",
                aliases=["Piedmont-Smith", "Isabel Piedmont-Smith"],
                politician_slug="isabel-piedmont-smith",
                politician_id="uuid-ips",
                district_label="District 5",
            ),
            RosterMember(
                name="Council President Asare",
                aliases=["Asare", "Sydney Asare"],
                politician_slug="sydney-asare",
                politician_id="uuid-a",
                district_label="At-Large",
            ),
            RosterMember(
                name="City Clerk Bolden",
                aliases=["Bolden", "Nicole Bolden"],
                politician_slug="nicole-bolden",
                politician_id="uuid-b",
                district_label=None,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Segment merging
# ---------------------------------------------------------------------------


def test_merge_adjacent_segments_retains_end_time_for_contained_segment():
    segments = [
        Segment(
            segment_id=0,
            start_time=10.0,
            end_time=20.0,
            speaker_label="SPEAKER_00",
            speaker_name="Alex Smith",
            text="First",
            words=[Word(word="First", start=10.0, end=11.0)],
        ),
        Segment(
            segment_id=1,
            start_time=12.0,
            end_time=14.0,
            speaker_label="SPEAKER_00",
            speaker_name="Alex Smith",
            text="second",
            words=[Word(word="second", start=12.0, end=13.0)],
        ),
    ]

    merged = merge_adjacent_segments(segments)

    assert len(merged) == 1
    assert merged[0].end_time == 20.0
    assert merged[0].text == "First second"
    assert [word.word for word in merged[0].words] == ["First", "second"]


def test_merge_adjacent_segments_extends_to_later_end_time():
    segments = [
        Segment(
            segment_id=0,
            start_time=10.0,
            end_time=12.0,
            speaker_label="SPEAKER_00",
            speaker_name="Alex Smith",
            text="First",
        ),
        Segment(
            segment_id=1,
            start_time=12.5,
            end_time=15.0,
            speaker_label="SPEAKER_00",
            speaker_name="Alex Smith",
            text="second",
        ),
    ]

    merged = merge_adjacent_segments(segments, gap_threshold=1.0)

    assert len(merged) == 1
    assert merged[0].end_time == 15.0
    assert merged[0].text == "First second"


def test_merge_adjacent_segments_is_idempotent():
    # Re-merging already-merged segments changes nothing (so the pipeline can run
    # the merge at identify AND again at export without harm).
    segments = [
        Segment(segment_id=0, start_time=0.0, end_time=3.0, speaker_label="A",
                speaker_name="Bass", text="hello"),
        Segment(segment_id=1, start_time=3.2, end_time=5.0, speaker_label="A",
                speaker_name="Bass", text="world"),
        Segment(segment_id=2, start_time=5.1, end_time=7.0, speaker_label="B",
                speaker_name="Host", text="hi"),
    ]
    once = merge_adjacent_segments(segments)
    twice = merge_adjacent_segments([Segment.from_dict(s.to_dict()) for s in once])
    assert len(once) == len(twice) == 2
    assert [s.text for s in twice] == ["hello world", "hi"]




# ---------------------------------------------------------------------------
# CSIDENT-04: SpeakerMapping model — politician_slug and politician_id
# ---------------------------------------------------------------------------


def test_SpeakerMapping_to_dict_includes_politician_slug():
    """to_dict() includes politician_slug and politician_id when set."""
    m = SpeakerMapping(
        speaker_label="SPEAKER_00",
        politician_slug="isabel-piedmont-smith",
        politician_id="uuid-ips",
    )
    d = m.to_dict()
    assert "politician_slug" in d
    assert d["politician_slug"] == "isabel-piedmont-smith"
    assert "politician_id" in d
    assert d["politician_id"] == "uuid-ips"


def test_SpeakerMapping_to_dict_omits_politician_slug_when_none():
    """to_dict() does NOT include politician_slug/politician_id when None."""
    m = SpeakerMapping(speaker_label="SPEAKER_00")
    d = m.to_dict()
    assert "politician_slug" not in d
    assert "politician_id" not in d


def test_SpeakerMapping_from_dict_round_trips_politician_fields():
    """from_dict() round-trips politician_slug and politician_id."""
    original = {
        "speaker_label": "SPEAKER_00",
        "politician_slug": "x-slug",
        "politician_id": "y-id",
    }
    m = SpeakerMapping.from_dict(original)
    assert m.politician_slug == "x-slug"
    assert m.politician_id == "y-id"
    # Round-trip back through to_dict
    d = m.to_dict()
    assert d["politician_slug"] == "x-slug"
    assert d["politician_id"] == "y-id"


def test_SpeakerMapping_from_dict_backward_compat():
    """from_dict() handles old dicts without politician fields (backward compat)."""
    old_dict = {"speaker_label": "SPEAKER_00"}
    m = SpeakerMapping.from_dict(old_dict)
    assert m.politician_slug is None
    assert m.politician_id is None


# ---------------------------------------------------------------------------
# CSIDENT-03: roster_names_for_prompt with district labels
# ---------------------------------------------------------------------------


def test_roster_names_for_prompt_includes_district_labels():
    """Output includes district labels in parentheses."""
    roster = _make_roster()
    result = roster_names_for_prompt(roster)
    assert "- Councilmember Piedmont-Smith (District 5)" in result
    assert "- Council President Asare (At-Large)" in result


def test_roster_names_for_prompt_omits_district_when_none():
    """Members with district_label=None have no parenthetical."""
    roster = _make_roster()
    result = roster_names_for_prompt(roster)
    # Bolden has district_label=None, should NOT have "()" or "(None)"
    assert "- City Clerk Bolden" in result
    assert "Bolden ()" not in result
    assert "Bolden (None)" not in result


def test_roster_names_for_prompt_empty_roster():
    """Empty roster returns empty string."""
    roster = Roster(city="X", body="Y", members=[])
    assert roster_names_for_prompt(roster) == ""


# ---------------------------------------------------------------------------
# CSIDENT-02: Pattern matcher roster-gated surname rejection
# ---------------------------------------------------------------------------


def test_pattern_matcher_roster_rejects_phantom():
    """apply_pattern_matching with roster rejects phantom name 'Piafra'."""
    segments = [
        Segment(
            segment_id=0, start_time=0.0, end_time=5.0,
            speaker_label="SPEAKER_00",
            text="The chair recognizes Councilmember Piafra",
        ),
        Segment(
            segment_id=1, start_time=5.0, end_time=10.0,
            speaker_label="SPEAKER_01",
            text="Thank you.",
        ),
    ]
    roster = _make_roster()
    candidates = apply_pattern_matching(segments, roster=roster)
    # Piafra is not in roster -- should be rejected
    for label, mappings in candidates.items():
        for m in mappings:
            assert "Piafra" not in (m.speaker_name or ""), \
                f"Phantom 'Piafra' should be rejected by roster gating"


def test_pattern_matcher_roster_accepts_real_member():
    """apply_pattern_matching with roster keeps real name 'Piedmont-Smith'."""
    segments = [
        Segment(
            segment_id=0, start_time=0.0, end_time=5.0,
            speaker_label="SPEAKER_00",
            text="The chair recognizes Councilmember Piedmont-Smith",
        ),
        Segment(
            segment_id=1, start_time=5.0, end_time=10.0,
            speaker_label="SPEAKER_01",
            text="Thank you.",
        ),
    ]
    roster = _make_roster()
    candidates = apply_pattern_matching(segments, roster=roster)
    # Piedmont-Smith IS in roster -- should have a mapping
    found = False
    for label, mappings in candidates.items():
        for m in mappings:
            if "Piedmont-Smith" in (m.speaker_name or ""):
                found = True
    assert found, "Real member 'Piedmont-Smith' should pass roster gating"


def test_pattern_matcher_roster_none_allows_all():
    """apply_pattern_matching without roster allows all names (backward compat)."""
    segments = [
        Segment(
            segment_id=0, start_time=0.0, end_time=5.0,
            speaker_label="SPEAKER_00",
            text="The chair recognizes Councilmember Piafra",
        ),
        Segment(
            segment_id=1, start_time=5.0, end_time=10.0,
            speaker_label="SPEAKER_01",
            text="Thank you.",
        ),
    ]
    candidates = apply_pattern_matching(segments, roster=None)
    # Without roster, Piafra should be allowed through
    found = False
    for label, mappings in candidates.items():
        for m in mappings:
            if "Piafra" in (m.speaker_name or ""):
                found = True
    assert found, "Without roster, phantom 'Piafra' should be allowed through"


# ---------------------------------------------------------------------------
# CSIDENT-04: correct_mappings populates politician identity
# ---------------------------------------------------------------------------


def test_correct_mappings_populates_politician_slug():
    """correct_mappings sets politician_slug/politician_id on roster-matched mappings."""
    roster = _make_roster()
    mappings = {
        "SPEAKER_01": SpeakerMapping(
            speaker_label="SPEAKER_01",
            speaker_name="Councilmember Piedmont-Smith",
            confidence=0.90,
            id_method="pattern",
        ),
    }
    result = correct_mappings(mappings, roster)
    assert result["SPEAKER_01"].politician_slug == "isabel-piedmont-smith"
    assert result["SPEAKER_01"].politician_id == "uuid-ips"


def test_correct_mappings_populates_id_when_slug_none():
    """correct_mappings carries politician_id even when the roster member's slug is None."""
    roster = Roster(
        city="Bloomington",
        body="bloomington-common-council",
        members=[
            RosterMember(
                name="Jane Candidate",
                aliases=["Candidate", "Jane Candidate"],
                politician_slug=None,
                politician_id="uuid-cand",
                district_label="District 3",
            ),
        ],
    )
    mappings = {
        "SPEAKER_01": SpeakerMapping(
            speaker_label="SPEAKER_01",
            speaker_name="Jane Candidate",
            confidence=0.90,
            id_method="pattern",
        ),
    }
    result = correct_mappings(mappings, roster)
    assert result["SPEAKER_01"].politician_id == "uuid-cand"
    assert result["SPEAKER_01"].politician_slug is None


def test_correct_mappings_leaves_slug_none_for_non_match():
    """correct_mappings leaves politician_slug=None for non-roster speakers."""
    roster = _make_roster()
    mappings = {
        "SPEAKER_01": SpeakerMapping(
            speaker_label="SPEAKER_01",
            speaker_name="John Public",
            confidence=0.90,
            id_method="pattern",
        ),
    }
    result = correct_mappings(mappings, roster)
    assert result["SPEAKER_01"].politician_slug is None


# ---------------------------------------------------------------------------
# Roster correction must not reassign a confident voice/human identity to a
# different member via fuzzy surname matching (regression: a 1.000 voice match
# for "Alexis Smithey" was overwritten with "Piedmont-Smith" because an
# alias-generated bare "Smith" token fuzzy-matched "Smithey" at 0.83).
# ---------------------------------------------------------------------------

def _collision_roster():
    return Roster(
        city="Bloomington",
        body="bloomington-common-council",
        members=[
            RosterMember(
                name="Councilmember Piedmont-Smith",
                aliases=["Piedmont-Smith", "Smith", "Isabel Piedmont-Smith"],
                politician_slug="isabel-piedmont-smith",
                politician_id="uuid-ips",
                district_label="District 1",
            ),
        ],
    )


def test_correct_speaker_name_allow_fuzzy_false_skips_fuzzy_reassignment():
    roster = _collision_roster()
    assert correct_speaker_name("Alexis Smithey", roster, allow_fuzzy=True) == "Councilmember Piedmont-Smith"
    assert correct_speaker_name("Alexis Smithey", roster, allow_fuzzy=False) == "Alexis Smithey"


def test_correct_mappings_does_not_reassign_voice_match():
    roster = _collision_roster()
    mappings = {
        "S1": SpeakerMapping(
            speaker_label="S1", speaker_name="Alexis S. Smithey",
            confidence=0.99, id_method="voice_profile",
        ),
    }
    correct_mappings(mappings, roster)
    assert mappings["S1"].speaker_name == "Alexis S. Smithey"  # not reassigned
    assert mappings["S1"].politician_slug is None              # not mislinked


def test_correct_mappings_does_not_reassign_human_confirmed():
    roster = _collision_roster()
    mappings = {
        "S1": SpeakerMapping(
            speaker_label="S1", speaker_name="Alexis S. Smithey",
            confidence=1.0, id_method="human_confirmed",
        ),
    }
    correct_mappings(mappings, roster)
    assert mappings["S1"].speaker_name == "Alexis S. Smithey"


def test_correct_mappings_still_fuzzy_corrects_pattern_names():
    # Weak (pattern/LLM) names still get fuzzy correction — typo fixing preserved.
    roster = _collision_roster()
    mappings = {
        "S1": SpeakerMapping(
            speaker_label="S1", speaker_name="Smithey",
            confidence=0.80, id_method="llm",
        ),
    }
    correct_mappings(mappings, roster)
    assert mappings["S1"].speaker_name == "Councilmember Piedmont-Smith"


def test_voice_match_exact_roster_name_still_links():
    # An authoritative voice match whose name IS a roster member still links.
    roster = _make_roster()
    mappings = {
        "S1": SpeakerMapping(
            speaker_label="S1", speaker_name="Councilmember Piedmont-Smith",
            confidence=0.99, id_method="voice_profile",
        ),
    }
    correct_mappings(mappings, roster)
    assert mappings["S1"].politician_slug == "isabel-piedmont-smith"


# ---------------------------------------------------------------------------
# CSIDENT-01/02 integration: identify_speakers phantom elimination
# ---------------------------------------------------------------------------


def test_identify_speakers_phantom_eliminated():
    """identify_speakers with roster eliminates phantom names from final mappings."""
    segments = [
        Segment(
            segment_id=0, start_time=0.0, end_time=5.0,
            speaker_label="SPEAKER_00",
            text="The chair recognizes Councilmember Piafra",
        ),
        Segment(
            segment_id=1, start_time=5.0, end_time=10.0,
            speaker_label="SPEAKER_01",
            text="Thank you, I appreciate that.",
        ),
    ]
    roster = _make_roster()
    mappings = identify_speakers(
        segments=segments,
        speaker_embeddings={},
        stored_profiles=None,
        llm_identify_fn=None,
        roster=roster,
    )
    # Phantom 'Piafra' should not appear in any final mapping
    for label, m in mappings.items():
        assert "Piafra" not in (m.speaker_name or ""), \
            f"Phantom 'Piafra' should not survive into final mappings (found on {label})"


# ---------------------------------------------------------------------------
# Wire 1: match_voice_profiles propagates a matched profile's identity
# ---------------------------------------------------------------------------

import numpy as np
from src.identify import match_voice_profiles
from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord


def _profile_db_with(identity: bool):
    centroid = np.array([1.0, 0.0, 0.0])
    prof = StoredProfile(
        speaker_id="essentials:john-hamilton" if identity else "hamilton_john",
        display_name="John Hamilton",
        embeddings=[EmbeddingRecord(centroid, "m0")],
        centroid=centroid,
        meetings_seen=["m0"],
        politician_slug="john-hamilton" if identity else None,
        politician_id="uuid-ham" if identity else None,
    )
    return ProfileDB(profiles={prof.speaker_id: prof})


def test_voice_match_carries_identity():
    db = _profile_db_with(identity=True)
    centroids = {"essentials:john-hamilton": db.profiles["essentials:john-hamilton"].centroid}
    speaker_embeddings = {"SPEAKER_00": np.array([1.0, 0.0, 0.0])}
    out = match_voice_profiles(speaker_embeddings, centroids, profile_db=db)
    assert out["SPEAKER_00"].politician_slug == "john-hamilton"
    assert out["SPEAKER_00"].politician_id == "uuid-ham"


def test_voice_match_no_identity_stays_none():
    db = _profile_db_with(identity=False)
    centroids = {"hamilton_john": db.profiles["hamilton_john"].centroid}
    speaker_embeddings = {"SPEAKER_00": np.array([1.0, 0.0, 0.0])}
    out = match_voice_profiles(speaker_embeddings, centroids, profile_db=db)
    assert out["SPEAKER_00"].politician_slug is None
    assert out["SPEAKER_00"].politician_id is None


# ---------------------------------------------------------------------------
# _carry_link: a politician link survives a same-person override, not a rename
# ---------------------------------------------------------------------------

from src.identify import _carry_link


def test_carry_link_same_name_preserves_identity():
    prior = SpeakerMapping(speaker_label="S0", speaker_name="John Hamilton",
                           confidence=0.9, politician_slug="john-hamilton",
                           politician_id="uuid-ham")
    new = SpeakerMapping(speaker_label="S0", speaker_name="John Hamilton",
                         confidence=0.95)
    out = _carry_link(prior, new)
    assert out.politician_slug == "john-hamilton"
    assert out.politician_id == "uuid-ham"


def test_carry_link_same_name_carries_id_when_slug_none():
    prior = SpeakerMapping(speaker_label="S0", speaker_name="Jane Doe",
                           confidence=0.9, politician_id="uuid-cand",
                           politician_slug=None)
    new = SpeakerMapping(speaker_label="S0", speaker_name="Jane Doe",
                         confidence=0.95)
    out = _carry_link(prior, new)
    assert out.politician_id == "uuid-cand"
    assert out.politician_slug is None


def test_carry_link_different_name_drops_identity():
    prior = SpeakerMapping(speaker_label="S0", speaker_name="John Hamilton",
                           confidence=0.9, politician_slug="john-hamilton",
                           politician_id="uuid-ham")
    new = SpeakerMapping(speaker_label="S0", speaker_name="Jane Smith",
                         confidence=0.95)
    out = _carry_link(prior, new)
    assert out.politician_slug is None
    assert out.politician_id is None


def test_carry_link_does_not_overwrite_existing_link():
    prior = SpeakerMapping(speaker_label="S0", speaker_name="John Hamilton",
                           politician_slug="john-hamilton", politician_id="uuid-ham")
    new = SpeakerMapping(speaker_label="S0", speaker_name="John Hamilton",
                         politician_slug="other-slug", politician_id="uuid-other")
    out = _carry_link(prior, new)
    assert out.politician_slug == "other-slug"


# ---------------------------------------------------------------------------
# Voice match must never surface a profile KEY (slug) as a human name
# ---------------------------------------------------------------------------

def test_voice_match_never_emits_profile_slug_as_name():
    """A match to a profile with no display_name must NOT use the profile key
    (a slug like 'hilton_steve') as the speaker name — leave it unnamed +
    needs_review so a human names it, rather than publishing a slug."""
    import numpy as np
    from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord
    c = np.array([1.0, 0.0, 0.0])
    prof = StoredProfile(speaker_id="hilton_steve", display_name="",
                         embeddings=[EmbeddingRecord(c, "m0")], centroid=c,
                         meetings_seen=["m0"])
    db = ProfileDB(profiles={"hilton_steve": prof})
    out = match_voice_profiles({"SPEAKER_00": np.array([1.0, 0.0, 0.0])},
                               {"hilton_steve": c}, profile_db=db)
    m = out["SPEAKER_00"]
    assert m.speaker_name is None          # not the slug "hilton_steve"
    assert m.needs_review is True
    assert m.id_method.startswith("voice_profile")


def test_voice_match_uses_display_name_when_present():
    import numpy as np
    from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord
    c = np.array([1.0, 0.0, 0.0])
    prof = StoredProfile(speaker_id="hilton_steve", display_name="Steve Hilton",
                         embeddings=[EmbeddingRecord(c, "m0")], centroid=c,
                         meetings_seen=["m0"], politician_slug="steve-hilton")
    db = ProfileDB(profiles={"hilton_steve": prof})
    out = match_voice_profiles({"SPEAKER_00": np.array([1.0, 0.0, 0.0])},
                               {"hilton_steve": c}, profile_db=db)
    m = out["SPEAKER_00"]
    assert m.speaker_name == "Steve Hilton"
    assert m.politician_slug == "steve-hilton"
    assert m.needs_review is False


# ---------------------------------------------------------------------------
# Identity-collision guard: two distinct diarized voices are two distinct
# people. A shared identity across labels is a mis-ID (typically an LLM guess
# that reuses a name already claimed by a voice match), NOT a merge. The guard
# keeps the single strongest mapping and demotes the rest to needs_review.
#
# Regression: interview-chris-swanson-wdiv, where SPEAKER_00 (Ty Steele, the
# unenrolled interviewer) was LLM-guessed as "Chris Swanson" — the same person
# as voice-matched SPEAKER_01 — then merge_adjacent_segments collapsed all 142
# segments into one "Chris Swanson" block.
# ---------------------------------------------------------------------------


def test_merge_does_not_collapse_distinct_speakers_sharing_a_name():
    """Two different diarized labels mislabeled with the same name must NOT merge.

    This is the 142->1 collapse: merge keys on diarization identity
    (speaker_label), never on a display name that a mis-ID can duplicate.
    """
    segments = [
        Segment(segment_id=0, start_time=0.0, end_time=5.0,
                speaker_label="SPEAKER_00", speaker_name="Chris Swanson",
                text="So tell me about your campaign."),
        Segment(segment_id=1, start_time=5.1, end_time=10.0,
                speaker_label="SPEAKER_01", speaker_name="Chris Swanson",
                text="Happy to. My focus is public safety."),
        Segment(segment_id=2, start_time=10.1, end_time=15.0,
                speaker_label="SPEAKER_00", speaker_name="Chris Swanson",
                text="And on housing?"),
    ]

    merged = merge_adjacent_segments(segments)

    assert len(merged) == 3
    assert [s.speaker_label for s in merged] == \
        ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]


def test_identify_speakers_rejects_duplicate_person_across_labels():
    """When two labels resolve to the same person, the stronger wins and the
    weaker is un-named, unlinked, and flagged for review."""
    import numpy as np
    from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord

    v_chris = np.array([1.0, 0.0, 0.0])
    v_other = np.array([0.0, 1.0, 0.0])
    prof = StoredProfile(
        speaker_id="essentials:chris-swanson",
        display_name="Chris Swanson",
        embeddings=[EmbeddingRecord(v_chris, "m0")],
        centroid=v_chris,
        meetings_seen=["m0"],
        politician_slug="chris-swanson",
        politician_id="uuid-chris",
    )
    db = ProfileDB(profiles={prof.speaker_id: prof})
    stored = {"essentials:chris-swanson": v_chris}
    embeddings = {"SPEAKER_00": v_other, "SPEAKER_01": v_chris}

    segments = [
        Segment(segment_id=0, start_time=0.0, end_time=5.0,
                speaker_label="SPEAKER_00", text="So tell me about your campaign."),
        Segment(segment_id=1, start_time=5.0, end_time=10.0,
                speaker_label="SPEAKER_01", text="Happy to. My focus is public safety."),
    ]

    # The LLM wrongly guesses the unenrolled interviewer is Chris Swanson too.
    def llm_fn(segs, maps):
        return {"SPEAKER_00": SpeakerMapping(
            speaker_label="SPEAKER_00", speaker_name="Chris Swanson",
            confidence=0.75, id_method="llm")}

    mappings = identify_speakers(
        segments=segments,
        speaker_embeddings=embeddings,
        stored_profiles=stored,
        llm_identify_fn=llm_fn,
        profile_db=db,
    )

    # SPEAKER_01 (voice profile, ~1.0) keeps the identity.
    assert mappings["SPEAKER_01"].speaker_name == "Chris Swanson"
    assert mappings["SPEAKER_01"].politician_id == "uuid-chris"
    # SPEAKER_00 (llm, 0.75) must NOT also be Chris Swanson.
    assert mappings["SPEAKER_00"].speaker_name != "Chris Swanson"
    assert mappings["SPEAKER_00"].politician_id is None
    assert mappings["SPEAKER_00"].needs_review is True


def test_identify_speakers_dedupes_shared_politician_id_across_labels():
    """Collision by politician_id (different name spellings, same person) is
    also caught: only one label keeps the link."""
    import numpy as np
    from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord

    v_chris = np.array([1.0, 0.0, 0.0])
    v_other = np.array([0.0, 1.0, 0.0])
    prof = StoredProfile(
        speaker_id="essentials:chris-swanson",
        display_name="Chris Swanson",
        embeddings=[EmbeddingRecord(v_chris, "m0")],
        centroid=v_chris,
        meetings_seen=["m0"],
        politician_slug="chris-swanson",
        politician_id="uuid-chris",
    )
    db = ProfileDB(profiles={prof.speaker_id: prof})
    stored = {"essentials:chris-swanson": v_chris}
    embeddings = {"SPEAKER_00": v_other, "SPEAKER_01": v_chris}
    segments = [
        Segment(segment_id=0, start_time=0.0, end_time=5.0,
                speaker_label="SPEAKER_00", text="Question."),
        Segment(segment_id=1, start_time=5.0, end_time=10.0,
                speaker_label="SPEAKER_01", text="Answer."),
    ]

    # LLM returns a different spelling but pre-linked to the same politician_id.
    def llm_fn(segs, maps):
        return {"SPEAKER_00": SpeakerMapping(
            speaker_label="SPEAKER_00", speaker_name="C. Swanson",
            confidence=0.75, id_method="llm",
            politician_slug="chris-swanson", politician_id="uuid-chris")}

    mappings = identify_speakers(
        segments=segments,
        speaker_embeddings=embeddings,
        stored_profiles=stored,
        llm_identify_fn=llm_fn,
        profile_db=db,
    )

    linked = [l for l, m in mappings.items() if m.politician_id == "uuid-chris"]
    assert linked == ["SPEAKER_01"]
    assert mappings["SPEAKER_00"].politician_id is None
    assert mappings["SPEAKER_00"].needs_review is True


# ---------------------------------------------------------------------------
# CREC layer: the Congressional Record is authoritative for WHO spoke.
# ---------------------------------------------------------------------------


def _two_segments():
    return [
        Segment(segment_id=0, start_time=0.0, end_time=5.0, speaker_label="SPEAKER_00",
                text="I move to proceed to the healthcare funding bill"),
        Segment(segment_id=1, start_time=5.0, end_time=10.0, speaker_label="SPEAKER_01",
                text="I rise in strong support of the healthcare measure"),
    ]


def test_crec_layer_confident_overrides_and_sets_mapping():
    crec = {
        "SPEAKER_00": SpeakerMapping(
            speaker_label="SPEAKER_00", speaker_name="Mitch McConnell", confidence=0.9,
            id_method="congressional_record", local_slug="congress-M000355"),
    }
    out = identify_speakers(_two_segments(), {}, crec_mappings=crec)
    assert out["SPEAKER_00"].speaker_name == "Mitch McConnell"
    assert out["SPEAKER_00"].id_method == "congressional_record"
    # SPEAKER_01 had no CREC mapping and no other layer identified it -> review
    assert out["SPEAKER_01"].needs_review is True


def test_crec_layer_ambiguous_flags_review():
    crec = {
        "SPEAKER_00": SpeakerMapping(
            speaker_label="SPEAKER_00", needs_review=True, speaker_status="unidentified"),
    }
    out = identify_speakers(_two_segments(), {}, crec_mappings=crec)
    assert out["SPEAKER_00"].needs_review is True
    assert out["SPEAKER_00"].speaker_name is None


def test_crec_layer_same_member_across_labels_kept_as_split():
    # Two labels that the Record confidently resolves to the SAME member are a
    # legitimate diarization split of one talkative speaker (e.g. a floor manager),
    # not a mis-ID — both must keep the name, not get deduped away.
    crec = {
        "SPEAKER_00": SpeakerMapping(
            speaker_label="SPEAKER_00", speaker_name="Mitch McConnell", confidence=0.9,
            id_method="congressional_record", local_slug="congress-M000355"),
        "SPEAKER_01": SpeakerMapping(
            speaker_label="SPEAKER_01", speaker_name="Mitch McConnell", confidence=0.7,
            id_method="congressional_record", local_slug="congress-M000355"),
    }
    out = identify_speakers(_two_segments(), {}, crec_mappings=crec)
    named = sorted(lbl for lbl, m in out.items() if m.speaker_name == "Mitch McConnell")
    assert named == ["SPEAKER_00", "SPEAKER_01"]   # both kept (diarization split)


from src.identify import _dedupe_identities


def test_dedupe_crec_wins_over_llm_collision_regardless_of_confidence():
    # A Record-verified member colliding with an LLM guess of the same name is the
    # exact mis-ID the guard exists to catch: keep CREC (even at lower confidence),
    # demote the LLM guess.
    mappings = {
        "A": SpeakerMapping(speaker_label="A", speaker_name="Jane Doe", confidence=0.5,
                            id_method="congressional_record", local_slug="congress-D000001"),
        "B": SpeakerMapping(speaker_label="B", speaker_name="Jane Doe", confidence=0.75,
                            id_method="llm"),
    }
    _dedupe_identities(mappings)
    assert mappings["A"].speaker_name == "Jane Doe"      # CREC survives despite lower conf
    assert mappings["A"].id_method == "congressional_record"
    assert mappings["B"].speaker_name is None            # LLM guess demoted
    assert mappings["B"].id_method == "collision"
    assert mappings["B"].needs_review is True


def test_dedupe_still_guards_non_crec_collision():
    # No CREC involved -> original guard intact: highest-confidence wins, rest demoted.
    mappings = {
        "A": SpeakerMapping(speaker_label="A", speaker_name="Jane Doe", confidence=0.8, id_method="llm"),
        "B": SpeakerMapping(speaker_label="B", speaker_name="Jane Doe", confidence=0.6, id_method="voice"),
    }
    _dedupe_identities(mappings)
    assert mappings["A"].speaker_name == "Jane Doe"
    assert mappings["B"].speaker_name is None
    assert mappings["B"].id_method == "collision"


def test_dedupe_keeps_all_crec_and_drops_llm_in_three_way_group():
    mappings = {
        "A": SpeakerMapping(speaker_label="A", speaker_name="Jane Doe", confidence=0.9,
                            id_method="congressional_record", local_slug="congress-D000001"),
        "B": SpeakerMapping(speaker_label="B", speaker_name="Jane Doe", confidence=0.6,
                            id_method="congressional_record", local_slug="congress-D000001"),
        "C": SpeakerMapping(speaker_label="C", speaker_name="Jane Doe", confidence=0.99, id_method="llm"),
    }
    _dedupe_identities(mappings)
    kept = sorted(l for l, m in mappings.items() if m.speaker_name == "Jane Doe")
    assert kept == ["A", "B"]                 # both CREC labels kept
    assert mappings["C"].id_method == "collision"   # LLM demoted even at 0.99 conf
