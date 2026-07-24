from scripts.verify_source import (
    normalize, verbatim_runs, candidate_phrases, parse_youtube,
    longest_verbatim_match, phrase_found, check_source, MIN_RUN_WORDS,
)


# --- normalize ---

def test_normalize_lowercases():
    assert normalize("Hello World") == "hello world"

def test_normalize_drops_bracketed_insertions():
    assert normalize("we need [more] funding") == "we need funding"

def test_normalize_drops_ellipses():
    assert normalize("taxes are too high… we must act") == "taxes are too high we must act"
    assert normalize("taxes are too high... we must act") == "taxes are too high we must act"

def test_normalize_strips_punctuation_and_collapses_whitespace():
    assert normalize("Hello,   World!!  Foo-bar.") == "hello world foo bar"

def test_normalize_empty_input():
    assert normalize("") == ""
    assert normalize(None) == ""


# --- verbatim_runs ---
# A curated quote is a chain of spoken spans joined by editorial edits (… elisions and
# [bracketed] insertions). Nothing runs contiguously across an edit, so verbatim_runs
# splits there and returns each contiguous, normalized span on its own.

def test_verbatim_runs_splits_at_unicode_ellipsis():
    assert verbatim_runs("we must act now … on the roads") == ["we must act now", "on the roads"]

def test_verbatim_runs_splits_at_ascii_ellipsis():
    assert verbatim_runs("we must act now ... on the roads") == ["we must act now", "on the roads"]

def test_verbatim_runs_splits_at_bracket_insertion():
    # Editorial [more] is not in the source, and text on either side of it is not
    # contiguous in the source either -> two runs, never one spanning the bracket.
    assert verbatim_runs("we need [more] funding for schools") == ["we need", "funding for schools"]

def test_verbatim_runs_single_run_when_no_edits():
    assert verbatim_runs("taxes are too high we must act") == ["taxes are too high we must act"]

def test_verbatim_runs_empty():
    assert verbatim_runs("") == []
    assert verbatim_runs(None) == []
    assert verbatim_runs("[all bracketed]") == []


# --- candidate_phrases (now the quote's contiguous verbatim spans) ---

def test_candidate_phrases_are_verbatim_runs():
    quote = "we must act now … on [the] roads today"
    assert candidate_phrases(quote) == verbatim_runs(quote)

def test_candidate_phrases_never_span_an_edit():
    # "high" and "we" are on opposite sides of the elision; no phrase should join them.
    phrases = candidate_phrases("taxes are too high … we must cut spending")
    assert all("high we" not in p for p in phrases)

def test_candidate_phrases_empty_quote():
    assert candidate_phrases("") == []
    assert candidate_phrases(None) == []


# --- longest_verbatim_match ---

def test_longest_match_full_run_present():
    n, words = longest_verbatim_match(["we must act now on the roads"],
                                      ["Well I think we must act now on the roads today"])
    assert n == 7
    assert words == "we must act now on the roads".split()

def test_longest_match_tolerates_dropped_filler_inside_run():
    # Curator dropped "um" that the ASR captured; the run no longer matches as a whole,
    # but its longest contiguous sub-run still does (and that's what we key off).
    n, words = longest_verbatim_match(["drive down energy costs for families"],
                                      ["we will drive down energy costs um for families"])
    assert n == 4
    assert words == "drive down energy costs".split()

def test_longest_match_absent_run():
    n, words = longest_verbatim_match(["moon landing hoax conspiracy grift"],
                                      ["we discussed infrastructure spending yesterday"])
    assert n == 0
    assert words == []

def test_longest_match_across_normalized_haystack_with_brackets():
    n, _ = longest_verbatim_match(["we must act now"], ["We must act now, [Gov.] said."])
    assert n == 4

def test_longest_match_does_not_span_edits_in_quote():
    # A cleaned quote whose two contiguous runs each appear in a synthetic segment list,
    # but whose across-edit junction ("protect ... water ... and drive") does NOT appear
    # contiguously anywhere. The match must come from a within-run span, never the junction.
    quote = "We must protect [the] water … and drive down energy costs for families."
    segments = [
        "We must protect the water supply here in Michigan.",
        "We will drive down energy costs for families across the state.",
    ]
    n, words = longest_verbatim_match(verbatim_runs(quote), segments)
    # longest genuine contiguous run is "drive down energy costs for families" (6 words)
    assert n == 6
    assert words == "drive down energy costs for families".split()
    assert n >= MIN_RUN_WORDS


# --- phrase_found ---

def test_phrase_found_true_when_long_run_present():
    phrases = ["taxes are too high we must"]
    segments = ["Well I think taxes are too high we must act now on this"]
    assert phrase_found(phrases, segments) is True

def test_phrase_found_false_when_absent():
    phrases = ["taxes are too high we must"]
    segments = ["I think spending is out of control and we need reform"]
    assert phrase_found(phrases, segments) is False

def test_phrase_found_false_for_short_run_below_threshold():
    # Only 4 contiguous words present -> below MIN_RUN_WORDS -> not a confident match.
    phrases = ["we must act now"]
    segments = ["We must act now, [Gov.] said."]
    assert phrase_found(phrases, segments) is False

def test_phrase_found_false_for_empty_phrases():
    assert phrase_found([], ["some segment text"]) is False


# --- parse_youtube ---

def test_parse_youtube_v_param():
    vid, t = parse_youtube("https://www.youtube.com/watch?v=qRNZ0kuA49k")
    assert vid == "qRNZ0kuA49k"
    assert t is None

def test_parse_youtube_short_url():
    vid, t = parse_youtube("https://youtu.be/qRNZ0kuA49k")
    assert vid == "qRNZ0kuA49k"

def test_parse_youtube_embed_url():
    vid, t = parse_youtube("https://www.youtube.com/embed/qRNZ0kuA49k")
    assert vid == "qRNZ0kuA49k"

def test_parse_youtube_with_timestamp():
    vid, t = parse_youtube("https://www.youtube.com/watch?v=qRNZ0kuA49k&t=123")
    assert vid == "qRNZ0kuA49k"
    assert t == 123

def test_parse_youtube_none_for_non_youtube():
    assert parse_youtube("https://example.com/article") == (None, None)

def test_parse_youtube_none_for_empty():
    assert parse_youtube(None) == (None, None)
    assert parse_youtube("") == (None, None)


# --- check_source (integration over a fake read-only DB) ---

class _FakeCursor:
    def __init__(self, meeting, segments):
        self._meeting, self._segments, self._last = meeting, segments, None
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql, params=None):
        if "FROM meetings.meetings" in sql:
            self._last = "meeting"
        elif "FROM meetings.segments" in sql:
            self._last = "segments"
        else:
            self._last = None
    def fetchone(self):
        return self._meeting if self._last == "meeting" else None
    def fetchall(self):
        return list(self._segments) if self._last == "segments" else []

class _FakeConn:
    def __init__(self, meeting, segments):
        self._m, self._s = meeting, segments
    def cursor(self, cursor_factory=None):
        return _FakeCursor(self._m, self._s)


def _row(quote_text, candidate="Jane Doe",
         source_url="https://www.youtube.com/watch?v=qRNZ0kuA49k"):
    return dict(id="q1", topic_key="data-centers", race_id="r1",
                candidate=candidate, quote_text=quote_text, source_url=source_url)

# A cleaned quote (ellipsis + bracket + dropped filler) that is genuinely in the transcript.
_GENUINE = "We must protect [the] water … and drive down energy costs for families."
_GENUINE_SEGMENTS = [
    dict(text="We must protect the water supply here in Michigan.",
         speaker_name="Jane Doe", start_time=10),
    dict(text="We will drive down energy costs for families across the state.",
         speaker_name="Jane Doe", start_time=40),
]


def test_check_source_verifies_cleaned_quote_with_edits():
    conn = _FakeConn({"id": "m1"}, _GENUINE_SEGMENTS)
    assert check_source(conn, _row(_GENUINE)) is None

def test_check_source_flags_genuinely_missourced_quote():
    conn = _FakeConn({"id": "m1"}, _GENUINE_SEGMENTS)
    row = _row("The moon is made of green cheese and I will legislate accordingly.")
    f = check_source(conn, row)
    assert f is not None and f.check_id == "source-unverified"

def test_check_source_flags_speaker_mismatch_when_present_but_wrong_speaker():
    # Quote IS in the transcript, but the matched segment is attributed to someone else
    # (a real diarization mislabel this check must keep catching).
    conn = _FakeConn({"id": "m1"}, _GENUINE_SEGMENTS)
    f = check_source(conn, _row(_GENUINE, candidate="Bob Smith"))
    assert f is not None and f.check_id == "source-speaker-mismatch"

def test_check_source_flags_when_video_not_ingested():
    conn = _FakeConn(None, [])
    f = check_source(conn, _row(_GENUINE))
    assert f is not None and f.check_id == "source-not-ingested"

def test_check_source_skips_non_video_source():
    conn = _FakeConn({"id": "m1"}, _GENUINE_SEGMENTS)
    assert check_source(conn, _row(_GENUINE, source_url="https://example.com/article")) is None
