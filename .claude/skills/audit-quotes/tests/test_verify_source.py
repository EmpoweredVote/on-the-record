from scripts.verify_source import normalize, candidate_phrases, parse_youtube, phrase_found


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


# --- candidate_phrases ---

def test_candidate_phrases_returns_interior_windows():
    # 20-word quote so interior slicing (words[2:-2]) kicks in.
    quote = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
    phrases = candidate_phrases(quote, n=6, k=3)
    assert len(phrases) <= 3
    assert all(len(p.split()) == 6 for p in phrases)
    # None of the phrases should start with the very first word or contain the very last word,
    # since interior avoids the first/last two words.
    for p in phrases:
        assert not p.startswith("one two")
        assert "twenty" not in p

def test_candidate_phrases_short_quote_fallback():
    # Fewer than n=6 words -> whole (normalized) quote as the single phrase.
    assert candidate_phrases("short quote here", n=6, k=3) == ["short quote here"]

def test_candidate_phrases_empty_quote():
    assert candidate_phrases("", n=6, k=3) == []
    assert candidate_phrases(None, n=6, k=3) == []

def test_candidate_phrases_respects_k_limit():
    quote = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
    phrases = candidate_phrases(quote, n=6, k=2)
    assert len(phrases) <= 2


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


# --- phrase_found ---

def test_phrase_found_true_when_interior_phrase_present():
    phrases = ["taxes are too high we"]
    segments = ["Well I think taxes are too high we must act now on this"]
    assert phrase_found(phrases, segments) is True

def test_phrase_found_false_when_absent():
    phrases = ["taxes are too high we"]
    segments = ["I think spending is out of control and we need reform"]
    assert phrase_found(phrases, segments) is False

def test_phrase_found_false_for_empty_phrases():
    assert phrase_found([], ["some segment text"]) is False

def test_phrase_found_matches_across_normalized_concat():
    # phrase spans across... not required, but confirm normalization applies to haystack too.
    phrases = ["we must act now"]
    segments = ["We must act now, [Gov.] said."]
    assert phrase_found(phrases, segments) is True
