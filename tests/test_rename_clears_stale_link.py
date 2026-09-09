"""A human rename must not let a stale identity link survive the correction.

Regression for the 2026-02-25-council contamination: an earlier auto-pass
mislinked speakers (voice-profile collisions / llm), the names were corrected by
hand, but the wrong politician_slug stayed attached — so enrollment (which keys
on politician_slug) wrote each voice into the wrong person's profile.

This file pins rename_speaker, which is the TERMINAL review's path
(run_local.py:3300, 3324). The GUI route deliberately does NOT behave this way:
it calls rename_preserving_identity, whose contrasting cases are at the bottom
of this file. If a change makes the tests above pass only by moving the terminal
onto the preserving behaviour, that is the regression, not the fix.
"""
from __future__ import annotations

from src.models import SpeakerMapping
from src.review import rename_speaker
from src.roster import Roster, RosterMember


def _roster():
    return Roster(
        city="",
        body="Bloomington Common Council",
        members=[
            RosterMember(name="Isak Nti Asare", aliases=["Isak Nti Asare", "Asare"],
                         politician_slug="isak-nti-asare", politician_id="uuid-asare"),
            RosterMember(name="Hopi Stosberg", aliases=["Hopi Stosberg", "Stosberg"],
                         politician_slug="hopi-h-stosberg", politician_id="uuid-stosberg"),
        ],
    )


def test_rename_rederives_link_dropping_stale_cross_link():
    # Stale link to Stosberg; human corrects the NAME to Asare.
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Hopi Stosberg",
        politician_slug="hopi-h-stosberg", politician_id="uuid-stosberg",
        id_method="voice_profile")}
    rename_speaker(mappings, [], "S0", "Isak Nti Asare", roster=_roster())
    m = mappings["S0"]
    assert m.speaker_name == "Isak Nti Asare"
    assert m.politician_slug == "isak-nti-asare"   # re-derived, NOT stale stosberg
    assert m.politician_id == "uuid-asare"


def test_rename_to_nonroster_name_clears_stale_link():
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Hopi Stosberg",
        politician_slug="hopi-h-stosberg", politician_id="uuid-stosberg")}
    rename_speaker(mappings, [], "S0", "Jane Q Public", roster=_roster())
    m = mappings["S0"]
    assert m.politician_slug is None
    assert m.politician_id is None


def test_rename_without_roster_clears_stale_link_on_name_change():
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Old Name",
        politician_slug="hopi-h-stosberg", politician_id="uuid-stosberg")}
    rename_speaker(mappings, [], "S0", "Different Person", roster=None)
    assert mappings["S0"].politician_slug is None
    assert mappings["S0"].politician_id is None


def test_rename_clears_stale_local_link_on_name_change():
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Old Name",
        local_slug="old-name", local_role="staff")}
    rename_speaker(mappings, [], "S0", "Someone Else", roster=None)
    assert mappings["S0"].local_slug is None
    assert mappings["S0"].local_role is None


def test_rename_spelling_fix_rederives_same_correct_link():
    # Same person, spelling tidy-up; the correct link survives via re-derivation.
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Asare",
        politician_slug="isak-nti-asare", politician_id="uuid-asare")}
    rename_speaker(mappings, [], "S0", "Isak Nti Asare", roster=_roster())
    assert mappings["S0"].politician_slug == "isak-nti-asare"


def test_rename_noop_preserves_existing_link():
    # No name change must not disturb a correct (possibly manually-pasted) link.
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Isak Nti Asare",
        politician_slug="isak-nti-asare", politician_id="uuid-asare")}
    rename_speaker(mappings, [], "S0", "Isak Nti Asare", roster=_roster())
    assert mappings["S0"].politician_slug == "isak-nti-asare"


# --- The GUI's path: rename_preserving_identity. The contrast with every test
# --- above is the point — same rename, opposite treatment of the identity.

from src.enroll import resolve_mapping_enrollment
from src.review import rename_preserving_identity


def test_preserving_rename_keeps_a_roster_link_a_plain_rename_would_drop():
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Hopi Stosburg",
        politician_slug="hopi-h-stosberg", politician_id="uuid-stosberg")}
    rename_preserving_identity(mappings, [], "S0", "Hopi Stosberg", roster=None)
    m = mappings["S0"]
    assert m.speaker_name == "Hopi Stosberg"
    assert m.politician_slug == "hopi-h-stosberg"
    assert m.politician_id == "uuid-stosberg"


def test_preserving_rename_keeps_a_local_person():
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Frank OConner",
        local_slug="frank-oconnor", local_role="public_comment")}
    rename_preserving_identity(mappings, [], "S0", "Frank O'Connor", roster=None)
    m = mappings["S0"]
    assert m.local_slug == "frank-oconnor"
    assert m.local_role == "public_comment"
    # Still exactly ONE identity (ev-accounts migration 623).
    assert m.politician_slug is None and m.politician_id is None


def test_a_preserved_link_still_enrolls_under_the_linked_person():
    """THE HAZARD rename_speaker's comment names: resolve_mapping_enrollment keys
    on politician_id ahead of the name, so a link that survives a rename decides
    whose voice profile this speaker's embedding joins. For a spelling fix that
    is exactly right — the person the curator deliberately linked. Asserted
    directly rather than inferred from the rename, because the rename passing
    says nothing about which profile the voice lands in."""
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Isak Nti Asari",
        politician_slug="isak-nti-asare", politician_id="uuid-asare")}
    rename_preserving_identity(mappings, [], "S0", "Isak Nti Asare", roster=_roster())

    key, slug, pid = resolve_mapping_enrollment(mappings["S0"])
    assert key == "essentials:uuid-asare"   # the linked person, not a name slug
    assert (slug, pid) == ("isak-nti-asare", "uuid-asare")


def test_a_preserving_rename_does_not_relink_to_a_roster_lookalike():
    """The preserved link must WIN over rename_speaker's roster re-derivation.
    Renaming a speaker who is linked to Asare to the roster name 'Hopi Stosberg'
    re-derives uuid-stosberg inside rename_speaker; the snapshot must put the
    deliberate link back, so enrollment cannot silently move person."""
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Isak Nti Asare",
        politician_slug="isak-nti-asare", politician_id="uuid-asare")}
    rename_preserving_identity(mappings, [], "S0", "Hopi Stosberg", roster=_roster())
    assert mappings["S0"].politician_id == "uuid-asare"
    assert resolve_mapping_enrollment(mappings["S0"])[0] == "essentials:uuid-asare"


def test_a_preserving_rename_keeps_an_unidentified_handle():
    """local_slug on an unidentified speaker is the synthetic
    unidentified-<meeting>-<label> handle whose whole purpose is keeping two
    distinct unknown speakers off one enrollment key. Nulling it dropped
    resolve_mapping_enrollment back to the name, merging unrelated strangers —
    the collision clear_local_person refuses to cause. So this is a fix, not
    merely a preservation."""
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Unidentified Speaker",
        local_slug="unidentified-2026-02-04-council-s0",
        speaker_status="unidentified")}
    rename_preserving_identity(mappings, [], "S0", "Man in the red jacket", roster=None)
    m = mappings["S0"]
    assert m.local_slug == "unidentified-2026-02-04-council-s0"
    assert resolve_mapping_enrollment(m)[0] == "local:unidentified-2026-02-04-council-s0"


def test_a_speaker_with_no_identity_still_gets_one_from_the_roster():
    """The guard on the snapshot. Restoring unconditionally would wipe the link
    rename_speaker derives for a freshly typed roster name — a real feature, and
    the only way a GUI rename attaches an identity on its own."""
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Unknown")}
    rename_preserving_identity(mappings, [], "S0", "Isak Nti Asare", roster=_roster())
    assert mappings["S0"].politician_id == "uuid-asare"
    assert mappings["S0"].politician_slug == "isak-nti-asare"


def test_a_preserving_rename_still_renames_the_segments():
    """Everything rename_speaker does to the NAME must be untouched — the
    snapshot restores identity fields only."""
    from src.models import Segment

    segs = [Segment(segment_id=0, start_time=0.0, end_time=5.0,
                    speaker_label="S0", text="hi", speaker_name="Old Name")]
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Old Name", local_slug="old-name",
        local_role="staff")}
    res = rename_preserving_identity(mappings, segs, "S0", "New Name", roster=None)
    assert segs[0].speaker_name == "New Name"
    assert mappings["S0"].id_method == "human_review"
    assert mappings["S0"].confidence == 1.0
    assert res.alias_suggestion == "Old Name"
