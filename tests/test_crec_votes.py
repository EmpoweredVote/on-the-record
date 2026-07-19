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
    assert v.positions["PRESENT"] == ["Gohmert"]        # bug 1: ANSWERED ``PRESENT'' now parsed
    assert v.positions["NOT_VOTING"] == ["Fudge", "Gabbard", "Higgins (LA)", "McNerney", "Norton"]  # bug 2: junk rejected
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


def test_rollcallvote_timestamp_defaults_none_and_is_settable():
    v = RollCallVote(438, "q", {"YEA": ["Adams"]})
    assert v.timestamp is None
    v.timestamp = 102.64
    assert v.timestamp == 102.64


def test_parse_outcome_agreed_to_from_fixture():
    text = (FIX / "granule_vote_block.txt").read_text()
    v = parse_votes(text)[0]
    assert v.outcome == "Agreed to"
    assert v.passed is True


def _block(outcome_line: str) -> str:
    return (
        "The question is on agreeing to the amendment.\n"
        "                             [Roll No. 200]\n"
        "                               AYES--1\n"
        "     Adams\n"
        f"  {outcome_line}\n"
        "  The result of the vote was announced as above recorded.\n"
    )


def test_parse_outcome_rejected():
    v = parse_votes(_block("So the amendment was rejected."))[0]
    assert v.outcome == "Rejected"
    assert v.passed is False


def test_parse_outcome_passed():
    v = parse_votes(_block("So the bill was passed."))[0]
    assert v.outcome == "Passed"
    assert v.passed is True


def test_parse_outcome_negated_is_fail():
    v = parse_votes(_block("So the motion was not agreed to."))[0]
    assert v.outcome == "Not agreed to"
    assert v.passed is False


def test_parse_outcome_plural_were_agreed_to():
    v = parse_votes(_block("So the amendments were agreed to."))[0]
    assert v.outcome == "Agreed to"
    assert v.passed is True


def test_parse_outcome_suspend_and_pass_takes_final_verb():
    line = "So (two-thirds being in the affirmative) the rules were suspended and the bill was passed."
    v = parse_votes(_block(line))[0]
    assert v.outcome == "Passed"
    assert v.passed is True


def test_parse_outcome_absent_is_none():
    v = parse_votes(_block("The Clerk announced the tally."))[0]
    assert v.outcome is None
    assert v.passed is None


def test_rollcallvote_outcome_defaults_none():
    v = RollCallVote(1, "q", {"YEA": ["Adams"]})
    assert v.outcome is None and v.passed is None
