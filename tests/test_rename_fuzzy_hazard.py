"""A human rename must never fuzzy-reassign a name onto a different roster member.

`correct_speaker_name`'s strategy 4 fuzzy-matches the surname portion of a name
against roster aliases at a 0.80 ratio. That is the right behaviour for the
PIPELINE, whose input is an ASR/LLM guess full of phoneme errors. It is the wrong
behaviour for a rename, whose input is a name a human typed while looking at the
review card.

Measured against the real production rosters (~/CouncilScribe/config), the two
populations are not separable by any threshold:

  genuine unseen ASR typos of a roster surname   0.667 .. 0.941
  DIFFERENT real people                          0.615 .. 1.000

The 1.000 is the case below. `Councilmember Piedmont-Smith` carries the real
alias "Piedmont, Smith", whose `extract_surname` is "Smith" — so an ordinary
member of the public named "Jane Smith" scores a PERFECT ratio against a sitting
councilmember. Meanwhile `allow_fuzzy=False` costs 0 of 30 realistic curator
spellings on the per-body roster format that `refresh_roster.py` actually writes,
because those alias lists include the canonical surname.

This matters because `publish._upsert_local_people` writes `speaker_name` as a
local person's PUBLIC name and `_upsert_speakers` writes it as
`meetings.speakers.display_name`, and `politician_id` is what publish uses to
derive a meeting's races. A wrong match here reaches readers on the live site.
"""
from __future__ import annotations

from src.models import SpeakerMapping
from src.review import rename_speaker
from src.roster import Roster, RosterMember, correct_speaker_name


def _roster() -> Roster:
    """Alias lists copied from the real production council_roster.json."""
    return Roster(
        city="Bloomington",
        body="City Council",
        members=[
            RosterMember(
                name="Councilmember Piedmont-Smith",
                aliases=["Piedmont, Smith", "Piemont-Smith", "Piedmont Smith",
                         "Piemont-Spath", "Pima-Smith"],
                politician_slug="isabel-piedmont-smith",
                politician_id="uuid-ips",
            ),
            RosterMember(
                name="Councilmember Stosberg",
                aliases=["Sasseberg", "Sasseburg", "Stasbur", "Stossberg", "Stasberg"],
                politician_slug="hopi-h-stosberg",
                politician_id="uuid-stosberg",
            ),
        ],
    )


def test_the_hazard_is_real_fuzzy_rewrites_a_different_person():
    """Guard on the premise. If this stops holding, the tests below prove nothing."""
    roster = _roster()
    # An ordinary member of the public, fuzzy-matched onto a sitting councilmember
    # at a perfect 1.000 ratio via the alias "Piedmont, Smith" -> surname "Smith".
    assert correct_speaker_name("Jane Smith", roster) == "Councilmember Piedmont-Smith"
    # And the docstring's own example, at 0.83.
    assert correct_speaker_name("Alexis Smithey", roster) == "Councilmember Piedmont-Smith"
    # Strategies 1-3 alone leave both alone.
    assert correct_speaker_name("Jane Smith", roster, allow_fuzzy=False) == "Jane Smith"


def test_rename_keeps_a_different_persons_typed_name_verbatim():
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="SPEAKER_00")}
    res = rename_speaker(mappings, [], "S0", "Jane Smith", roster=_roster())
    assert res.new_name == "Jane Smith"
    assert mappings["S0"].speaker_name == "Jane Smith"


def test_rename_does_not_attach_a_fuzzy_matched_politician_link():
    """The second fuzzy hop. rename_speaker re-derives the link via
    resolve_enrollment_key, which calls correct_speaker_name AGAIN with fuzzy on.
    Keeping the name verbatim while still attaching the wrong politician_id would
    be worse than the original bug: the card shows the right name, so the curator
    gets no cue at all that publish will attribute this person's words to a
    councilmember."""
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="SPEAKER_00")}
    rename_speaker(mappings, [], "S0", "Jane Smith", roster=_roster())
    assert mappings["S0"].politician_slug is None
    assert mappings["S0"].politician_id is None


def test_rename_propagates_the_verbatim_name_to_segments():
    from src.models import Segment

    segs = [Segment(segment_id=0, start_time=0.0, end_time=1.0, speaker_label="S0",
                    text="hello")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="SPEAKER_00")}
    rename_speaker(mappings, segs, "S0", "Jane Smith", roster=_roster())
    assert segs[0].speaker_name == "Jane Smith"


# --- the legitimate normalisation this fix must NOT lose --------------------

def test_rename_still_normalises_an_exact_alias_and_links():
    """The desirable case from the design: a curator typing "Piedmont Smith"
    should still get the canonical spelling AND the matching link. That is
    strategy 2 (exact alias), which the fix keeps."""
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="SPEAKER_00")}
    res = rename_speaker(mappings, [], "S0", "Piedmont Smith", roster=_roster())
    assert res.new_name == "Councilmember Piedmont-Smith"
    assert mappings["S0"].politician_slug == "isabel-piedmont-smith"
    assert mappings["S0"].politician_id == "uuid-ips"


def test_rename_still_normalises_the_exact_canonical_name_and_links():
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="SPEAKER_00")}
    res = rename_speaker(mappings, [], "S0", "councilmember piedmont-smith",
                         roster=_roster())
    assert res.new_name == "Councilmember Piedmont-Smith"
    assert mappings["S0"].politician_id == "uuid-ips"


def test_rename_still_normalises_an_alias_appearing_as_a_word_and_links():
    """Strategy 3: "Council Member Sasseberg" -> the Stosberg alias "Sasseberg"."""
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="SPEAKER_00")}
    res = rename_speaker(mappings, [], "S0", "Council Member Sasseberg",
                         roster=_roster())
    assert res.new_name == "Councilmember Stosberg"
    assert mappings["S0"].politician_id == "uuid-stosberg"


def test_pipeline_correction_keeps_fuzzy_matching():
    """The fix is scoped to the rename path. correct_mappings, which corrects
    ASR/LLM guesses, must still repair an unseen misspelling — that is what
    strategy 4 is for."""
    from src.roster import correct_mappings

    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Stossburg",
                                     id_method="llm")}
    correct_mappings(mappings, _roster())
    assert mappings["S0"].speaker_name == "Councilmember Stosberg"
    assert mappings["S0"].politician_id == "uuid-stosberg"
