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


class _Cur:
    """Minimal cursor: returns canned rows per-query by matching a keyword.

    Honors a literal DISTINCT in the query text by deduping identical rows,
    the way the real database engine collapses identical rows for a
    `SELECT DISTINCT ...` query -- this lets a mocked test exercise the
    one-row-per-meeting invariant without a real database.
    """
    def __init__(self, speakers, segments):
        self._speakers, self._segments, self._last, self._sql = speakers, segments, None, ""
    def execute(self, sql, params=None):
        self._sql = sql.lower()
        self._last = "speakers" if "from meetings.speakers" in self._sql and "segments" not in self._sql else "segments"
    def fetchall(self):
        rows = self._speakers if self._last == "speakers" else self._segments
        if "distinct" in self._sql:
            rows = list(dict.fromkeys(rows))
        return rows
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, speakers, segments): self._s, self._g = speakers, segments
    def cursor(self, *a, **k): return _Cur(self._s, self._g)


def test_fetch_transcript_sources_assembles_speaker_labeled_text():
    # one meeting; candidate speaker id = 10; a moderator turn then the candidate's turn
    speakers = [("m1", "Debate", "https://site/m1", "https://youtu.be/x", "debate")]
    segments = [
        (0, 12.0, "Moderator", "What will you do on housing?"),
        (1, 20.0, "Karen Bass", "We will build 40,000 units by cutting permit timelines."),
    ]
    src = fetch_transcript_sources(_Conn(speakers, segments), "p1")
    assert len(src) == 1
    s = src[0]
    assert s.meeting_id == "m1" and s.video_url == "https://youtu.be/x"
    assert "Moderator: What will you do on housing?" in s.full_text
    assert "Karen Bass: We will build 40,000 units" in s.full_text
    assert (20.0, "We will build 40,000 units by cutting permit timelines.") in s.segments


def test_fetch_transcript_sources_dedups_speaker_split_within_one_meeting():
    # A diarization split can link the same politician to TWO speaker labels/rows
    # within the same meeting. The meetings query must select only meeting-scoped
    # columns so DISTINCT collapses these to one row -> one TranscriptSource.
    speakers = [
        ("m1", "Debate", "https://site/m1", "https://youtu.be/x", "debate"),
        ("m1", "Debate", "https://site/m1", "https://youtu.be/x", "debate"),
    ]
    segments = [
        (0, 12.0, "Moderator", "What will you do on housing?"),
        (1, 20.0, "Karen Bass", "We will build 40,000 units by cutting permit timelines."),
    ]
    src = fetch_transcript_sources(_Conn(speakers, segments), "p1")
    assert len(src) == 1
    assert src[0].meeting_id == "m1"
