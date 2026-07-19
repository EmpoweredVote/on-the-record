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


def test_build_floor_votes_projects_and_timestamps():
    import json
    from pathlib import Path
    from src.crec_votes import RollCallVote
    from src.crec_structure import CrecGranule
    from src.crec_floor import GranuleVotes, build_floor_votes

    FIX = Path(__file__).parent / "fixtures" / "timing"
    segs = json.loads((FIX / "house_vote_announcements.json").read_text())
    r438 = RollCallVote(438, "Smith amdt", {"YEA": ["x"] * 236, "NAY": ["y"] * 193})
    r439 = RollCallVote(439, "Speier amdt", {"YEA": ["x"] * 242, "NAY": ["y"] * 187})
    r440 = RollCallVote(440, "Speier amdt", {"YEA": ["x"] * 231, "NAY": ["y"] * 199})
    gv = GranuleVotes(granule=CrecGranule("g", "HOUSE", "NDAA", ""),
                      votes=[r438, r439, r440], members=[])

    class _FS:
        votes = [gv]

    fvs = build_floor_votes(_FS(), segs)
    assert [v.roll_number for v in fvs] == [438, 439, 440]
    assert (fvs[0].yea, fvs[0].nay) == (236, 193)
    assert fvs[0].timestamp == 102.64 and fvs[0].matched is True
    assert fvs[2].timestamp == 732.28 and fvs[2].tally_delta == 1


def test_floorvote_outcome_roundtrips():
    from src.models import FloorVote
    fv = FloorVote(438, "q", 236, 193, 0, 9, 102.6, 0, True,
                   outcome="Agreed to", passed=True)
    d = fv.to_dict()
    assert d["outcome"] == "Agreed to" and d["passed"] is True
    back = FloorVote.from_dict(d)
    assert back.outcome == "Agreed to" and back.passed is True


def test_floorvote_outcome_defaults_none_for_positional_construction():
    from src.models import FloorVote
    fv = FloorVote(1, "q", 1, 0, 0, 0, None, None, False)  # 9 positional, no outcome
    assert fv.outcome is None and fv.passed is None
    assert FloorVote.from_dict(fv.to_dict()).outcome is None


def test_build_floor_votes_carries_outcome():
    from types import SimpleNamespace
    from src.crec_votes import RollCallVote
    from src.crec_floor import build_floor_votes, FloorStructure, GranuleVotes
    rc = RollCallVote(438, "On the Smith amendment",
                      {"YEA": ["Adams"], "NAY": []},
                      outcome="Agreed to", passed=True)
    fs = FloorStructure(date="2019-07-11", chamber="house")
    fs.votes = [GranuleVotes(granule=SimpleNamespace(text=""), votes=[rc], members=[])]
    out = build_floor_votes(fs, [])  # no segments -> no timestamp, outcome still carried
    assert out[0].outcome == "Agreed to" and out[0].passed is True
