import time

from scripts.verify_source import (
    normalize, verbatim_runs, candidate_phrases, parse_youtube,
    longest_verbatim_match, phrase_found, check_source, MIN_RUN_WORDS,
    extract_page_text, starts_midsentence, cached_page_text,
    MIN_PAGE_WORDS, CACHE_TTL_SECONDS, nested_quotation,
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

def test_check_source_skips_non_video_source_when_no_fetcher_supplied():
    # DB-only default: written sources stay out of scope unless the caller opts into network I/O.
    conn = _FakeConn({"id": "m1"}, _GENUINE_SEGMENTS)
    assert check_source(conn, _row(_GENUINE, source_url="https://example.com/article")) is None


# --- extract_page_text (pure HTML -> text) ---

def test_extract_page_text_drops_script_and_style_content():
    html = "<html><head><style>.a{color:red}</style></head><body>" \
           "<script>var x = 'we must act now';</script><p>Real prose here.</p></body></html>"
    text = extract_page_text(html)
    assert "Real prose here." in text
    assert "color" not in text
    assert "var x" not in text

def test_extract_page_text_separates_block_elements():
    # Without a block separator "one" and "Two" would fuse into a phantom phrase.
    text = extract_page_text("<p>Sentence one.</p><p>Two starts here.</p>")
    assert "oneTwo" not in text
    assert "Sentence one." in text and "Two starts here." in text

def test_extract_page_text_decodes_entities():
    assert "don't" in extract_page_text("<p>we don&#39;t agree</p>")
    assert "&amp;" not in extract_page_text("<p>tax &amp; spend</p>")

def test_extract_page_text_preserves_sentence_punctuation():
    # starts_midsentence keys off the character before the match, so punctuation must survive.
    assert "." in extract_page_text("<p>First. Second.</p>")


# --- starts_midsentence (pure clip-boundary check) ---
# The WI-02 defect: a run that IS verbatim on the page but was cut out of the middle of a
# sentence, dropping the candidate's actual position, with no ellipsis marking the cut.

_WI02_PAGE = (
    "The best and only sure way to overturn Citizens United is to craft a Constitutional "
    "Amendment that places language \"in stone\" (so that no future SCOTUS can misinterpret it) "
    "that unlimited corporate money cannot be spent to sway elections, even without "
    "coordination with a particular candidate."
)
_WI02_CLIP = ("unlimited corporate money cannot be spent to sway elections, even without "
              "coordination with a particular candidate")

def test_starts_midsentence_true_for_unmarked_midsentence_clip():
    assert starts_midsentence(_WI02_CLIP, _WI02_PAGE) is True

def test_starts_midsentence_false_when_cut_is_marked_with_ellipsis():
    # A curator who marks the cut has disclosed it; that is the sanctioned form.
    assert starts_midsentence("… " + _WI02_CLIP, _WI02_PAGE) is False

def test_starts_midsentence_false_when_quote_begins_a_sentence():
    page = "We tried that already. Taxes are too high and working families feel it."
    assert starts_midsentence("Taxes are too high and working families feel it.", page) is False

def test_starts_midsentence_false_at_a_block_boundary():
    # First sentence of a new paragraph is a sentence start even with no terminator before it.
    page = extract_page_text("<p>A heading with no period</p><p>Taxes are too high today.</p>")
    assert starts_midsentence("Taxes are too high today.", page) is False

def test_starts_midsentence_false_after_an_introducing_colon():
    page = "His position is simple: taxes are too high and must come down."
    assert starts_midsentence("taxes are too high and must come down", page) is False

def test_starts_midsentence_false_when_the_source_itself_quotes_the_run():
    # Standard journalism: the article wraps the candidate's words in quotation marks inside a
    # reporter's sentence. The cut is the SOURCE's, faithfully reproduced — not a curator clip.
    page = ('He said he favors a single-payer model that includes private insurance, adding that '
            '"a government-paying program is the most moral and cost-effective way to go."')
    quote = "a government-paying program is the most moral and cost-effective way to go."
    assert starts_midsentence(quote, page) is False

def test_starts_midsentence_true_when_cut_falls_inside_the_sources_quotation():
    # Same shape, but the curator started partway INTO what the source quoted, dropping the
    # operative clause. The source's quotation mark is further left, so this still flags.
    page = ('Latz said "a single-payer program is the only path that works, because a '
            'government-paying program is the most moral way to go."')
    assert starts_midsentence("a government-paying program is the most moral way to go.", page) is True

def test_starts_midsentence_false_when_quote_opens_the_page():
    assert starts_midsentence("Taxes are too high.", "Taxes are too high. And so is spending.") is False

def test_starts_midsentence_false_when_run_is_not_on_the_page():
    assert starts_midsentence("moon landing hoax conspiracy grift", _WI02_PAGE) is False

def test_starts_midsentence_false_for_empty_inputs():
    assert starts_midsentence("", _WI02_PAGE) is False
    assert starts_midsentence(_WI02_CLIP, "") is False


# --- cached_page_text (real temp dir, injected fetcher — no HTTP) ---

def test_cached_page_text_fetches_once_and_reuses(tmp_path):
    calls = []
    def fetch(url):
        calls.append(url)
        return "Taxes are too high and working families feel it."
    first = cached_page_text("https://example.com/issues", tmp_path, fetch)
    second = cached_page_text("https://example.com/issues", tmp_path, fetch)
    assert first == second
    assert calls == ["https://example.com/issues"]

def test_cached_page_text_caches_a_failed_fetch(tmp_path):
    # A 403/timeout must not be retried for every quote citing that host during a sweep.
    calls = []
    def fetch(url):
        calls.append(url)
        return None
    assert cached_page_text("https://example.com/dead", tmp_path, fetch) is None
    assert cached_page_text("https://example.com/dead", tmp_path, fetch) is None
    assert len(calls) == 1

def test_cached_page_text_refetches_after_ttl_expiry(tmp_path):
    calls = []
    def fetch(url):
        calls.append(url)
        return f"page body number {len(calls)} with enough words to be real"
    cached_page_text("https://example.com/issues", tmp_path, fetch)
    for p in tmp_path.iterdir():                      # age the entry past its TTL
        stale = time.time() - CACHE_TTL_SECONDS - 60
        import os; os.utime(p, (stale, stale))
    cached_page_text("https://example.com/issues", tmp_path, fetch)
    assert len(calls) == 2

def test_cached_page_text_keys_distinct_urls_separately(tmp_path):
    def fetch(url):
        return f"body for {url}"
    a = cached_page_text("https://example.com/a", tmp_path, fetch)
    b = cached_page_text("https://example.com/b", tmp_path, fetch)
    assert a != b


# --- check_source: written-source path (fetch injected, never real HTTP) ---

_ISSUES_PAGE = (
    "Taxes. Working families in this district pay more than their share, and I will vote to "
    "cut the tax burden on them before we cut it for anyone else. " + _WI02_PAGE
)

def _written_row(quote_text, url="https://alexanderforhope.com/issues"):
    return _row(quote_text, source_url=url)

def _fetcher(text):
    return lambda url: text

def test_check_source_verifies_written_quote_present_on_the_page():
    row = _written_row("Working families in this district pay more than their share, and I "
                       "will vote to cut the tax burden on them before we cut it for anyone else.")
    assert check_source(None, row, fetch_page=_fetcher(_ISSUES_PAGE)) is None

def test_check_source_flags_written_quote_absent_from_the_page():
    row = _written_row("I will abolish the federal income tax in my first term in office.")
    f = check_source(None, row, fetch_page=_fetcher(_ISSUES_PAGE))
    assert f is not None and f.check_id == "source-unverified"
    assert f.severity == "high"

def test_check_source_flags_the_wi02_midsentence_clip():
    f = check_source(None, _written_row(_WI02_CLIP), fetch_page=_fetcher(_ISSUES_PAGE))
    assert f is not None and f.check_id == "source-midsentence-clip"

def test_check_source_accepts_the_same_clip_once_the_cut_is_marked():
    row = _written_row("… " + _WI02_CLIP)
    assert check_source(None, row, fetch_page=_fetcher(_ISSUES_PAGE)) is None

def test_check_source_flags_unfetchable_page():
    f = check_source(None, _written_row("anything at all here"), fetch_page=lambda url: None)
    assert f is not None and f.check_id == "source-unfetchable"
    assert f.severity == "medium"

def test_check_source_flags_js_shell_page_as_unfetchable_not_unverified():
    # An SPA shell returns a handful of words; that is "could not read", not "quote is fake".
    shell = " ".join(["loading"] * (MIN_PAGE_WORDS - 1))
    f = check_source(None, _written_row("I will abolish the federal income tax"),
                     fetch_page=_fetcher(shell))
    assert f is not None and f.check_id == "source-unfetchable"

def test_check_source_accepts_html_and_extracts_it():
    html = "<html><body><script>junk</script><p>" + _ISSUES_PAGE + "</p></body></html>"
    row = _written_row("Working families in this district pay more than their share")
    assert check_source(None, row, fetch_page=_fetcher(html)) is None

def test_check_source_does_not_fetch_aggregator_sources():
    # invalid-source / unquotable-source already own these; fetching them is wasted traffic.
    def boom(url):
        raise AssertionError(f"should not have fetched {url}")
    assert check_source(None, _written_row("whatever", "https://www.ontheissues.org/x.htm"),
                        fetch_page=boom) is None
    assert check_source(None, _written_row("whatever", "https://www.isidewith.com/c/1"),
                        fetch_page=boom) is None

def test_check_source_does_not_fetch_non_http_sources():
    def boom(url):
        raise AssertionError(f"should not have fetched {url}")
    assert check_source(None, _written_row("whatever", "not a url at all"), fetch_page=boom) is None

def test_check_source_written_path_carries_quote_identity_on_the_finding():
    f = check_source(None, _written_row("I will abolish the federal income tax"),
                     fetch_page=_fetcher(_ISSUES_PAGE))
    assert f.quote_id == "q1" and f.race_id == "r1" and f.topic_key == "data-centers"
    assert f.fix_class == "decision-required"


# --- editorial marks a verbatim match must survive ---
# A curated quote is never a raw span: it elides with `…`, inserts `[context]`, and the page it
# came from may use curly punctuation where the DB has straight. None of that is a sourcing
# defect, so none of it may produce a finding.

def test_check_source_matches_across_an_ellipsis_elision():
    row = _written_row("Working families in this district pay more than their share… "
                       "I will vote to cut the tax burden on them")
    assert check_source(None, row, fetch_page=_fetcher(_ISSUES_PAGE)) is None

def test_check_source_matches_around_a_bracketed_insertion():
    row = _written_row("Working families in this district pay more than their share, and I will "
                       "vote to cut the tax burden on [working families] before we cut it for "
                       "anyone else.")
    assert check_source(None, row, fetch_page=_fetcher(_ISSUES_PAGE)) is None

def test_check_source_matches_despite_smart_vs_straight_punctuation():
    page = ("Taxes and spending dominated the forum. " + "Some filler sentence here. " * 15 +
            "I won't raise taxes on working families in this district, not once.")
    row = _written_row("I won’t raise taxes on working families in this district")
    assert check_source(None, row, fetch_page=_fetcher(page)) is None


# --- nested_quotation (pure relayed-speech check) ---
# The TN-Governor defect (race ea27533a, 2026-08-02): Blackburn relaying what voters tell her,
# curated as her own pledge. The text is perfectly verbatim, so no amount of string matching
# against the page can catch it — only the punctuation and framing around the match can.

_TN_PAGE = (
    "Blackburn spent the morning in Sumner County talking about the border, which she says "
    "comes up at nearly every stop she makes across the state this summer. She told the crowd "
    "that the issue has moved to the top of the list for Republican primary voters here. "
    "\"People will say, 'Hey, let's make certain our communities are safe and let's pick up "
    "the pace deporting illegal aliens.'\" She then turned to the state budget, and to the "
    "coming fight over school vouchers when the legislature returns next session. "
    "Blackburn said, \"I will vote to finish the border wall in my first year as governor.\""
)
_TN_RELAYED = ("let's make certain our communities are safe and let's pick up the pace "
               "deporting illegal aliens")
_TN_OWN_WORDS = "I will vote to finish the border wall in my first year as governor"

def test_nested_quotation_flags_words_the_candidate_is_relaying():
    assert nested_quotation(_TN_RELAYED, _TN_PAGE) is not None

def test_nested_quotation_silent_on_the_candidates_own_quoted_words():
    # Same page, same article, ordinary journalism: `Blackburn said, "…"`. Must not flag.
    assert nested_quotation(_TN_OWN_WORDS, _TN_PAGE) is None

def test_nested_quotation_flags_when_the_quote_keeps_its_own_framing():
    # The other stored form of the same defect: the curator kept "People will say," and the
    # relayed words together. No page needed to see this one.
    quote = "People will say, 'Hey, let's make certain our communities are safe.'"
    assert nested_quotation(quote, _TN_PAGE) is not None

def test_nested_quotation_handles_curly_quotation_marks():
    page = ("Blackburn said, “People will say, ‘Hey, let’s make certain our communities are "
            "safe.’” She moved on to the budget.")
    assert nested_quotation("let’s make certain our communities are safe", page) is not None

def test_nested_quotation_flags_a_third_party_frame_without_nesting():
    page = "On the campaign trail, they tell me the tax code is rigged against small business owners."
    assert nested_quotation("the tax code is rigged against small business owners", page) is not None

def test_nested_quotation_ignores_a_frame_from_a_previous_sentence():
    # "Voters say" governs its own sentence, not the one the quote came from.
    page = "Voters say the economy is broken. She responded that we need lower taxes for families."
    assert nested_quotation("we need lower taxes for families", page) is None

def test_nested_quotation_does_not_treat_a_possessive_as_an_open_quotation():
    page = 'The mayor defended workers\' rights and said, "We will raise the minimum wage."'
    assert nested_quotation("We will raise the minimum wage.", page) is None

def test_nested_quotation_does_not_treat_an_apostrophe_as_an_open_quotation():
    page = 'She said, "We can\'t wait any longer to fix the roads in this county."'
    assert nested_quotation("We can't wait any longer to fix the roads in this county.", page) is None

def test_nested_quotation_ignores_a_candidates_own_rhetorical_setup():
    # "People say X. They're wrong." is the candidate's own words and a legitimate quote: the
    # frame is theirs, and no inner quotation follows it.
    quote = "People say we can't fix this. They're wrong, and I will prove it."
    page = "Asked about the backlog, she was blunt. " + quote
    assert nested_quotation(quote, page) is None

def test_nested_quotation_silent_when_the_run_is_not_on_the_page():
    # Absence is source-unverified's finding to make, not this one's.
    assert nested_quotation("moon landing hoax conspiracy grift", _TN_PAGE) is None

def test_nested_quotation_silent_for_empty_inputs():
    assert nested_quotation("", _TN_PAGE) is None
    assert nested_quotation(_TN_RELAYED, "") is None


# --- check_source: the nested-quotation finding ---

def test_check_source_flags_the_tn_relayed_quote_at_high_severity():
    f = check_source(None, _written_row(_TN_RELAYED), fetch_page=_fetcher(_TN_PAGE))
    assert f is not None and f.check_id == "source-nested-quotation"
    assert f.severity == "high"

def test_check_source_accepts_the_candidates_own_words_from_the_same_page():
    row = _written_row(_TN_OWN_WORDS)
    assert check_source(None, row, fetch_page=_fetcher(_TN_PAGE)) is None

def test_nested_quotation_outranks_the_midsentence_clip_finding():
    # The relayed quote also starts mid-sentence, so without ordering it would surface as
    # source-midsentence-clip at medium — the wrong defect, understated.
    assert starts_midsentence(_TN_RELAYED, _TN_PAGE) is True
    f = check_source(None, _written_row(_TN_RELAYED), fetch_page=_fetcher(_TN_PAGE))
    assert f.check_id == "source-nested-quotation"

def test_check_source_nested_finding_carries_quote_identity():
    f = check_source(None, _written_row(_TN_RELAYED), fetch_page=_fetcher(_TN_PAGE))
    assert f.quote_id == "q1" and f.race_id == "r1" and f.candidate == "Jane Doe"
    assert f.fix_class == "decision-required"


# --- nested quotation on the VIDEO path (ASR transcript, not a page) ---
# ASR seldom transcribes quotation marks, so on this path the framing signal carries the check
# and the nesting signal rarely fires. The framing words themselves survive transcription fine.

_RELAYED_SEGMENTS = [
    dict(text="Let me tell you what I hear when I am out in the counties.",
         speaker_name="Jane Doe", start_time=10),
    dict(text="People will say, hey, let's make certain our communities are safe and "
              "let's pick up the pace deporting illegal aliens.",
         speaker_name="Jane Doe", start_time=20),
    dict(text="We will drive down energy costs for families across the state.",
         speaker_name="Jane Doe", start_time=40),
]
_TRANSCRIPT_RELAYED = ("let's make certain our communities are safe and let's pick up the pace "
                       "deporting illegal aliens")

def test_check_source_flags_relayed_speech_in_a_transcript():
    conn = _FakeConn({"id": "m1"}, _RELAYED_SEGMENTS)
    f = check_source(conn, _row(_TRANSCRIPT_RELAYED))
    assert f is not None and f.check_id == "source-nested-quotation"
    assert f.severity == "high"

def test_check_source_accepts_the_candidates_own_words_in_the_same_transcript():
    conn = _FakeConn({"id": "m1"}, _RELAYED_SEGMENTS)
    row = _row("We will drive down energy costs for families across the state.")
    assert check_source(conn, row) is None

def test_check_source_does_not_let_a_moderators_frame_govern_the_candidate():
    # The frame belongs to the moderator's segment; the candidate's answer is their own position.
    # Segments are joined with newlines precisely so the frame cannot reach across the boundary.
    segs = [
        dict(text="People will say we should deport everyone. Is that your view?",
             speaker_name="Moderator", start_time=5),
        dict(text="We should deport everyone who came here illegally, and I will say so.",
             speaker_name="Jane Doe", start_time=15),
    ]
    conn = _FakeConn({"id": "m1"}, segs)
    row = _row("We should deport everyone who came here illegally")
    assert check_source(conn, row) is None

def test_check_source_flags_self_framed_quote_even_on_the_video_path():
    conn = _FakeConn({"id": "m1"}, _RELAYED_SEGMENTS)
    row = _row("People will say, 'Hey, let's make certain our communities are safe.'")
    f = check_source(conn, row)
    assert f is not None and f.check_id == "source-nested-quotation"

def test_check_source_flags_self_framed_quote_even_when_the_video_is_not_ingested():
    # Signal 1 needs no source at all, so an un-ingested video must not hide the defect behind
    # source-not-ingested.
    conn = _FakeConn(None, [])
    row = _row("People will say, 'Hey, let's make certain our communities are safe.'")
    f = check_source(conn, row)
    assert f is not None and f.check_id == "source-nested-quotation"

def test_check_source_flags_self_framed_quote_on_a_written_source_without_a_fetcher():
    # Same reason: no network needed, so the DB-only default still catches it.
    row = _row("People will say, 'Hey, let's make certain our communities are safe.'",
               source_url="https://example.com/article")
    f = check_source(None, row)
    assert f is not None and f.check_id == "source-nested-quotation"

def test_speaker_mismatch_still_outranks_the_relayed_check():
    # Wrong person entirely is the more fundamental defect; report that first.
    conn = _FakeConn({"id": "m1"}, _RELAYED_SEGMENTS)
    f = check_source(conn, _row(_TRANSCRIPT_RELAYED, candidate="Bob Smith"))
    assert f is not None and f.check_id == "source-speaker-mismatch"

def test_third_party_frame_does_not_cross_a_segment_boundary():
    # Direct check of the newline rule the transcript join relies on.
    assert nested_quotation("we need lower taxes for families",
                            "They tell me\nwe need lower taxes for families") is None
    assert nested_quotation("we need lower taxes for families",
                            "They tell me we need lower taxes for families") is not None


# --- apostrophes that are not quotation marks ---
# Regression tests for two false positives found by sweeping all 3,272 live quotes: an elided
# word and an ASR-split contraction, each read as an opening quote that never closes, marking
# everything after it in the transcript as nested.

def test_an_elided_word_does_not_open_a_quotation():
    page = "It's tough, 'cause remember we need lower taxes for families in this state."
    assert nested_quotation("we need lower taxes for families in this state", page) is None

def test_an_asr_split_oclock_does_not_open_a_quotation():
    # Whisper writes "o'clock" as "o 'clock", which looks exactly like an open quote.
    page = "We start again at 11 o 'clock. You get lower taxes for families in this state."
    assert nested_quotation("lower taxes for families in this state", page) is None

def test_a_decade_apostrophe_does_not_open_a_quotation():
    page = "Back in the '90s we had lower taxes for families in this state, and it worked."
    assert nested_quotation("lower taxes for families in this state", page) is None

def test_an_unclosed_mark_in_an_earlier_block_does_not_leak():
    # Quote depth is scoped to the current block, so one misread mark can't poison the document.
    page = "She said 'this is a stray open mark\nWe need lower taxes for families in this state."
    assert nested_quotation("We need lower taxes for families in this state", page) is None

def test_a_genuine_nested_quotation_still_flags_within_its_block():
    page = "Blackburn said, \"People will say, 'we need lower taxes for families in this state.'\""
    assert nested_quotation("we need lower taxes for families in this state", page) is not None
