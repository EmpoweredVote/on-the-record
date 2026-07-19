import json
from pathlib import Path
from src.crec_votes import RollCallVote
from src.crec_timing import (
    VoteAnnouncement, extract_announcements,
    VoteTiming, match_rolls_to_announcements, attach_vote_timestamps,
)

FIX = Path(__file__).parent / "fixtures" / "timing"


def _roll(n, yea, nay):
    return RollCallVote(n, "q", {"YEA": ["x"] * yea, "NAY": ["y"] * nay})


def test_extract_announcements_from_real_segments():
    segs = json.loads((FIX / "house_vote_announcements.json").read_text())
    anns = extract_announcements(segs)
    assert len(anns) == 3
    assert all(isinstance(a, VoteAnnouncement) for a in anns)
    assert (anns[0].yea, anns[0].nay) == (236, 193)
    assert (anns[1].yea, anns[1].nay) == (242, 187)
    assert (anns[2].yea, anns[2].nay) == (230, 199)
    assert anns[0].timestamp == 102.64
    assert anns[1].timestamp == 452.46
    assert anns[2].timestamp == 732.28


def test_extract_handles_ayes_and_and_variants():
    segs = [
        {"start_time": 5.0, "text": "On this vote, the ayes are 243. The nays are 187.",
         "words": [{"word": "ayes", "start": 5.5}]},
        {"start_time": 9.0, "text": "the yeas are 225 and the nays are 205, the amendment is adopted.",
         "words": [{"word": "yeas", "start": 9.2}]},
    ]
    anns = extract_announcements(segs)
    assert [(a.yea, a.nay) for a in anns] == [(243, 187), (225, 205)]
    assert anns[0].timestamp == 5.5


def test_extract_skips_non_vote_segments_and_falls_back_to_start_time():
    segs = [
        {"start_time": 1.0, "text": "The gentleman is recognized for five minutes.", "words": []},
        {"start_time": 2.0, "text": "the yeas are 100, the nays are 50.", "words": []},
    ]
    anns = extract_announcements(segs)
    assert len(anns) == 1
    assert (anns[0].yea, anns[0].nay, anns[0].timestamp) == (100, 50, 2.0)


def test_match_exact_and_off_by_one():
    rolls = [_roll(438, 236, 193), _roll(439, 242, 187), _roll(440, 231, 199)]
    anns = [
        VoteAnnouncement(236, 193, 102.64, ""),
        VoteAnnouncement(242, 187, 452.46, ""),
        VoteAnnouncement(230, 199, 732.28, ""),
    ]
    timings = match_rolls_to_announcements(rolls, anns)
    assert [(t.roll_number, t.timestamp, t.matched) for t in timings] == [
        (438, 102.64, True), (439, 452.46, True), (440, 732.28, True),
    ]
    assert timings[0].tally_delta == 0
    assert timings[2].tally_delta == 1


def test_match_skips_spurious_announcement_and_preserves_order():
    rolls = [_roll(438, 236, 193)]
    anns = [VoteAnnouncement(999, 1, 10.0, ""), VoteAnnouncement(236, 193, 102.64, "")]
    timings = match_rolls_to_announcements(rolls, anns)
    assert (timings[0].roll_number, timings[0].timestamp) == (438, 102.64)


def test_match_unmatched_roll_gets_none():
    rolls = [_roll(500, 300, 100)]
    anns = [VoteAnnouncement(236, 193, 102.64, "")]
    timings = match_rolls_to_announcements(rolls, anns)
    assert timings[0].matched is False
    assert timings[0].timestamp is None


def test_attach_sets_timestamp_on_matched_rolls():
    segs = json.loads((FIX / "house_vote_announcements.json").read_text())
    rolls = [_roll(438, 236, 193), _roll(439, 242, 187), _roll(440, 231, 199)]
    timings = attach_vote_timestamps(rolls, segs)
    assert [t.roll_number for t in timings] == [438, 439, 440]
    assert rolls[0].timestamp == 102.64
    assert rolls[1].timestamp == 452.46
    assert rolls[2].timestamp == 732.28


def test_absolutize_shifts_timestamps_by_offset():
    from src.crec_timing import absolutize_vote_timestamps
    rolls = [_roll(438, 236, 193), _roll(439, 242, 187)]
    rolls[0].timestamp = 102.6
    rolls[1].timestamp = 452.5
    out = absolutize_vote_timestamps(rolls, 14600.0)
    assert [round(r.timestamp, 1) for r in out] == [14702.6, 15052.5]
    # input not mutated (deep copy, mirrors clip.absolutize_meeting_times)
    assert rolls[0].timestamp == 102.6


def test_absolutize_no_offset_and_none_timestamp_unchanged():
    from src.crec_timing import absolutize_vote_timestamps
    r = _roll(500, 300, 100)          # timestamp defaults None (unmatched roll)
    matched = _roll(438, 236, 193); matched.timestamp = 102.6
    # None/0 offset -> returned unchanged (copy)
    assert absolutize_vote_timestamps([matched], None)[0].timestamp == 102.6
    assert absolutize_vote_timestamps([matched], 0)[0].timestamp == 102.6
    # None timestamp stays None even with an offset
    assert absolutize_vote_timestamps([r], 14600.0)[0].timestamp is None
