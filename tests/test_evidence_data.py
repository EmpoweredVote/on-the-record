import os
from src.evidence.data import database_url, fetch_transcript_sources


def test_database_url_prefers_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://from-env/x")
    assert database_url() == "postgres://from-env/x"


def test_database_url_reads_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    p = tmp_path / ".env"
    p.write_text('DATABASE_URL="postgres://from-file/y"\nOTHER=1\n')
    assert database_url(str(p)) == "postgres://from-file/y"


class _Cur2:
    """Returns speaker-id rows for the speakers-in-meeting query, else segment rows.

    Honors a literal DISTINCT in the meetings query by deduping identical rows,
    the way the real database engine collapses identical rows for a
    `SELECT DISTINCT ...` query -- this lets a mocked test exercise the
    one-row-per-meeting invariant without a real database.
    """
    def __init__(self, meetings, cand_ids, segments):
        self._meetings, self._cand_ids, self._segments, self._last, self._sql = meetings, cand_ids, segments, None, ""
    def execute(self, sql, params=None):
        self._sql = sql.lower()
        if "from meetings.speakers" in self._sql and "meetings.meetings" in self._sql: self._last = "meetings"
        elif "from meetings.speakers" in self._sql: self._last = "cand_ids"
        else: self._last = "segments"
    def fetchall(self):
        rows = {"meetings": self._meetings, "cand_ids": self._cand_ids, "segments": self._segments}[self._last]
        if self._last == "meetings" and "distinct" in self._sql:
            rows = list(dict.fromkeys(rows))
        return rows
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn2:
    def __init__(self, m, c, s): self._m, self._c, self._s = m, c, s
    def cursor(self, *a, **k): return _Cur2(self._m, self._c, self._s)


def test_fetch_transcript_sources_candidate_turns_plus_question_only():
    # one meeting; candidate's speaker id = 10; a moderator turn precedes the
    # candidate's turn (kept as eliciting context), a non-adjacent other speaker
    # turn follows (dropped -- not the candidate's own words, no context role).
    meetings = [("m1", "Debate", "https://site/m1", "https://youtu.be/x", "debate")]
    cand_ids = [(10,)]  # candidate's speaker id in this meeting
    segments = [  # (segment_index, start_time, speaker_id, speaker_name, text)
        (0, 12.0, 99, "Moderator", "What will you do on housing?"),
        (1, 20.0, 10, "Karen Bass", "We will build 40,000 units by cutting permit timelines."),
        (2, 40.0, 88, "Opponent",  "I disagree with that approach entirely."),
    ]
    s = fetch_transcript_sources(_Conn2(meetings, cand_ids, segments), "p1")[0]
    assert "Karen Bass: We will build 40,000 units" in s.full_text     # candidate turn
    assert "Moderator: What will you do on housing?" in s.full_text    # eliciting question kept
    assert "Opponent" not in s.full_text                               # other speaker dropped
    assert s.segments == [(20.0, "We will build 40,000 units by cutting permit timelines.")]


def test_fetch_transcript_sources_dedups_speaker_split_within_one_meeting():
    # A diarization split can link the same politician to TWO speaker labels/rows
    # within the same meeting. The meetings query must select only meeting-scoped
    # columns so DISTINCT collapses these to one row -> one TranscriptSource.
    meetings = [
        ("m1", "Debate", "https://site/m1", "https://youtu.be/x", "debate"),
        ("m1", "Debate", "https://site/m1", "https://youtu.be/x", "debate"),
    ]
    cand_ids = [(10,), (11,)]  # the split: two speaker rows linked to the same politician
    segments = [
        (0, 12.0, 99, "Moderator", "What will you do on housing?"),
        (1, 20.0, 10, "Karen Bass", "We will build 40,000 units by cutting permit timelines."),
    ]
    src = fetch_transcript_sources(_Conn2(meetings, cand_ids, segments), "p1")
    assert len(src) == 1
    assert src[0].meeting_id == "m1"
