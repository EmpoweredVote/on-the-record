import pytest

from src.models import SpeakerMapping
from src.review import LOCAL_SLUG_RE, default_local_slug


def test_default_local_slug_kebab_cases_the_name():
    assert default_local_slug("Susan Brackney", "SPEAKER_04") == "susan-brackney"


def test_default_local_slug_falls_back_to_the_label():
    assert default_local_slug(None, "SPEAKER_04") == "speaker-04"
    assert default_local_slug("   ", "SPEAKER_04") == "speaker-04"


def test_default_local_slug_output_is_always_valid():
    for name, label in [("Susan Brackney", "S0"), ("!!!", "S0"), ("!!!", "!!!"),
                        ("O'Brien-Smith, Jr.", "S1"), ("x" * 300, "S2")]:
        slug = default_local_slug(name, label)
        assert LOCAL_SLUG_RE.match(slug), f"({name!r}, {label!r}) produced {slug!r}"


def test_default_local_slug_is_bounded_at_one_hundred():
    assert len(default_local_slug("x" * 300, "S2")) == 100


from src.review import assign_local_person, clear_local_person


def test_assign_local_person_sets_slug_and_role():
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Susan Brackney")}
    m = assign_local_person(mappings, "S0", "susan-brackney", "public_comment")
    assert (m.local_slug, m.local_role) == ("susan-brackney", "public_comment")


def test_assign_local_person_clears_any_essentials_identity():
    """One identity per speaker (migration 623). A local person is not a roster
    politician, so making someone local drops the essentials link rather than
    leaving publish to suppress the contradiction."""
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Marcy Kaptur",
                                     politician_id="uuid-mk", politician_slug="marcy-kaptur")}
    m = assign_local_person(mappings, "S0", "marcy-kaptur", "official")
    assert m.politician_id is None
    assert m.politician_slug is None


def test_assign_local_person_creates_a_mapping_for_an_unmapped_label():
    mappings = {}
    m = assign_local_person(mappings, "S7", "jane-doe", "staff")
    assert mappings["S7"] is m
    assert m.speaker_label == "S7"


def test_assign_local_person_rejects_an_invalid_slug():
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    for bad in ["Susan Brackney", "-leading", "_leading", "", "x" * 101, "UPPER"]:
        with pytest.raises(ValueError):
            assign_local_person(mappings, "S0", bad, "staff")


def test_assign_local_person_refuses_a_slug_held_by_another_label():
    """Two diarized labels cannot be the same person."""
    mappings = {
        "S0": SpeakerMapping(speaker_label="S0", local_slug="susan-brackney"),
        "S1": SpeakerMapping(speaker_label="S1"),
    }
    with pytest.raises(ValueError, match="already used"):
        assign_local_person(mappings, "S1", "susan-brackney", "public_comment")


def test_assign_local_person_allows_reassigning_the_same_label():
    mappings = {"S0": SpeakerMapping(speaker_label="S0", local_slug="susan-brackney",
                                     local_role="public_comment")}
    m = assign_local_person(mappings, "S0", "susan-brackney", "staff")
    assert m.local_role == "staff"


def test_clear_local_person_unsets_both_fields():
    mappings = {"S0": SpeakerMapping(speaker_label="S0", local_slug="susan-brackney",
                                     local_role="public_comment")}
    m = clear_local_person(mappings, "S0")
    assert (m.local_slug, m.local_role) == (None, None)


def test_clear_local_person_on_unknown_label_is_a_noop():
    assert clear_local_person({}, "S9") is None


from src.review import identity_label


def test_identity_label_prefers_politician_id_over_a_local_slug():
    """politician_slug is NULL for ~99.4% of essentials politicians, so a federal
    speaker carrying politician_id plus the crec bioguide stash must not read as
    a local person. Mirrors src/enroll.py:215."""
    m = SpeakerMapping(speaker_label="S0", speaker_name="Marcy Kaptur",
                       politician_id="uuid-mk", local_slug="congress-K000009")
    assert identity_label(m) == "essentials:uuid-mk"


def test_identity_label_still_prefers_a_slug_when_present():
    m = SpeakerMapping(speaker_label="S0", politician_slug="marcy-kaptur")
    assert identity_label(m) == "essentials:marcy-kaptur"


def test_identity_label_reports_a_genuine_local_person():
    m = SpeakerMapping(speaker_label="S0", local_slug="susan-brackney")
    assert identity_label(m) == "local:susan-brackney"


from src.review import link_speaker


def test_link_speaker_clears_a_local_person():
    """One identity per speaker: an essentials link supersedes a local person, the
    mirror of assign_local_person clearing the essentials fields."""
    mappings = {"S0": SpeakerMapping(speaker_label="S0", local_slug="jo-doe",
                                     local_role="staff")}
    m = link_speaker(mappings, "S0", None, "uuid-jd")
    assert m.politician_id == "uuid-jd"
    assert m.local_slug is None
    assert m.local_role is None


def test_link_speaker_by_slug_also_clears_a_local_person():
    mappings = {"S0": SpeakerMapping(speaker_label="S0", local_slug="jo-doe",
                                     local_role="staff")}
    m = link_speaker(mappings, "S0", "jo-doe-politician", None)
    assert m.local_slug is None
    assert m.local_role is None


def test_unlinking_leaves_a_local_person_alone():
    """link_speaker(None, None) is the UNLINK path. Clearing the politician link must
    not destroy an unrelated local-person identity."""
    mappings = {"S0": SpeakerMapping(speaker_label="S0", local_slug="jo-doe",
                                     local_role="staff",
                                     politician_id="uuid-jd")}
    m = link_speaker(mappings, "S0", None, None)
    assert m.politician_id is None
    assert m.politician_slug is None
    assert m.local_slug == "jo-doe"
    assert m.local_role == "staff"
