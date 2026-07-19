from pathlib import Path
from src.crec_floor import FloorStructure, GranuleVotes, extract_floor_structure

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def test_extract_floor_structure_classifies_and_parses_votes():
    vote_text = (FIX / "granule_vote_block.txt").read_text()
    back_text = (FIX / "granule_backmatter.txt").read_text()
    mods = (FIX / "granule_vote_mods.xml").read_text()

    # One HOUSE granule list with two granules: a legislative (vote) one and a
    # back-matter one. granuleId encodes which htm/mods body to return.
    list_json = (
        '{"granules": ['
        '{"granuleClass": "HOUSE", "granuleId": "G-VOTE", "title": "NDAA"},'
        '{"granuleClass": "HOUSE", "granuleId": "G-BACK", '
        '"title": "Constitutional Authority Statement for H.R. 3694"}'
        ']}'
    )

    def fake_fetch(url: str) -> str:
        if "/granules?" in url:
            return list_json
        if "G-VOTE/mods" in url:
            return mods
        if "G-VOTE/htm" in url:
            return f"<pre>{vote_text}</pre>"
        if "G-BACK/htm" in url:
            return f"<pre>{back_text}</pre>"
        return "<pre></pre>"

    fs = extract_floor_structure("2019-07-11", "house", fetch=fake_fetch, api_key="k")
    assert isinstance(fs, FloorStructure)
    assert [g.granule_id for g in fs.agenda_granules] == ["G-VOTE"]
    assert fs.attention_granules == []
    assert fs.discarded == 1                      # the back-matter granule
    assert len(fs.votes) == 1
    gv = fs.votes[0]
    assert isinstance(gv, GranuleVotes)
    assert gv.votes[0].roll_number == 438
    # bioguide join: Adams (YEA) resolved from MODS
    adams = next(m for m in gv.members if m.surname == "Adams")
    assert adams.bioguide == "A000370"
    assert adams.position == "YEA"


def test_no_record_returns_none():
    def fake_fetch(url: str) -> str:
        raise RuntimeError("no package")

    assert extract_floor_structure("2018-10-13", "house", fetch=fake_fetch, api_key="k") is None


def test_floorvote_and_meeting_roundtrip():
    from src.models import FloorVote, Meeting
    fv = FloorVote(roll_number=438, question="On the Smith amendment", yea=236, nay=193,
                   present=0, not_voting=9, timestamp=102.6, tally_delta=0, matched=True)
    assert FloorVote.from_dict(fv.to_dict()) == fv
    m = Meeting(meeting_id="m1", city=None, date="2019-07-11", floor_votes=[fv])
    m2 = Meeting.from_dict(m.to_dict())
    assert m2.floor_votes == [fv]
    assert Meeting.from_dict({"meeting_id": "m2", "date": "2019-07-11"}).floor_votes == []
