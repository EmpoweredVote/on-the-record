from src.discovery.prefilter import (
    duration_signal, match_names, normalize, prefilter_item,
)


def test_normalize_strips_accents_case_punctuation():
    assert normalize("Verónica O'Brien-Smith!") == "veronica o brien smith"


def test_match_requires_full_name_not_last_name():
    names = ["Maria Delgado", "Cher"]
    hits = match_names("Delgado wins straw poll", "", names)
    assert hits == []  # last name alone is collision bait
    hits = match_names("Maria Delgado town hall on housing", "", names)
    assert hits == ["Maria Delgado"]


def test_single_token_names_never_match_at_stage_one():
    assert match_names("An evening with Cher", "", ["Cher"]) == []


def test_match_found_in_description_too():
    hits = match_names("Candidate forum", "Featuring Maria Delgado and others", ["Maria Delgado"])
    assert hits == ["Maria Delgado"]


def test_duration_signal_bands():
    assert duration_signal(None) == "unknown"
    assert duration_signal(3 * 60) == "short"
    assert duration_signal(12 * 60) == "neutral"
    assert duration_signal(40 * 60) == "long"


def test_prefilter_rejects_no_name_match():
    v = prefilter_item("City weather update", "", 3600, ["Maria Delgado"])
    assert not v.passed and v.reason == "no tracked candidate name"


def test_prefilter_rejects_short_clip_without_event_term():
    v = prefilter_item("Maria Delgado responds to poll", "", 90, ["Maria Delgado"])
    assert not v.passed and v.duration_signal == "short"


def test_prefilter_passes_short_clip_with_event_term():
    v = prefilter_item("Maria Delgado town hall highlights", "", 90, ["Maria Delgado"])
    assert v.passed


def test_prefilter_passes_short_qa_clip():
    v = prefilter_item("Q&A with Maria Delgado", "", 90, ["Maria Delgado"])
    assert v.passed


def test_prefilter_passes_short_clip_with_event_term_in_description():
    v = prefilter_item("Highlights: Maria Delgado", "From last night's town hall.",
                       90, ["Maria Delgado"])
    assert v.passed


def test_prefilter_passes_long_video_with_name():
    v = prefilter_item("Delgado vs. Ruiz: full debate", "Maria Delgado faces Ana Ruiz",
                       55 * 60, ["Maria Delgado", "Ana Ruiz"])
    assert v.passed and set(v.matched_names) == {"Maria Delgado", "Ana Ruiz"}
    assert v.duration_signal == "long"
