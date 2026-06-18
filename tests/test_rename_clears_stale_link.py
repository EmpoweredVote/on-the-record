"""A human rename must not let a stale identity link survive the correction.

Regression for the 2026-02-25-council contamination: an earlier auto-pass
mislinked speakers (voice-profile collisions / llm), the names were corrected by
hand, but the wrong politician_slug stayed attached — so enrollment (which keys
on politician_slug) wrote each voice into the wrong person's profile.
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
