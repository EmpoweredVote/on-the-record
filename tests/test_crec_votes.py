from pathlib import Path
from src.crec_votes import RollCallVote, parse_votes

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def test_parse_single_vote_block():
    text = (FIX / "granule_vote_block.txt").read_text()
    votes = parse_votes(text)
    assert len(votes) == 1
    v = votes[0]
    assert isinstance(v, RollCallVote)
    assert v.roll_number == 438
    assert v.positions["YEA"] == ["Adams", "Aguilar", "Allred", "Amash", "Axne"]
    assert v.positions["NAY"] == ["Abraham", "Aderholt", "Allen", "Amodei", "Armstrong"]
    assert v.positions["NOT_VOTING"] == ["Fudge", "Gabbard", "Higgins (LA)", "McNerney", "Norton"]
    assert "Smith" in v.question


def test_parse_two_votes_splits_on_roll_markers():
    text = (
        "The question is on agreeing to amendment A.\n"
        "                             [Roll No. 100]\n"
        "                               AYES--1\n"
        "     Adams\n"
        "  The result of the vote was announced as above recorded.\n"
        "The question is on agreeing to amendment B.\n"
        "                             [Roll No. 101]\n"
        "                               NOES--1\n"
        "     Abraham\n"
    )
    votes = parse_votes(text)
    assert [v.roll_number for v in votes] == [100, 101]
    assert votes[0].positions == {"YEA": ["Adams"]}
    assert votes[1].positions == {"NAY": ["Abraham"]}


def test_no_votes_returns_empty():
    assert parse_votes("Mr. SMITH. Mr. Speaker, I yield back.") == []
