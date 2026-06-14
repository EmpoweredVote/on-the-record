from src.models import SummarySection
from src.topics import substantive_sections, validate_topic_keys, build_classification_prompt


def _section(stype, title="T", idx_text="hello world"):
    return SummarySection(section_type=stype, title=title, content=idx_text,
                          start_time=0.0, end_time=1.0, start_segment=0, end_segment=1)


def test_substantive_sections_filters_by_type_and_keeps_index():
    sections = [
        _section("opening"),
        _section("discussion"),
        _section("roll_call"),
        _section("vote"),
    ]
    result = substantive_sections(sections)
    # returns (original_index, section) pairs for substantive types only
    assert [i for i, _ in result] == [1, 3]


def test_validate_drops_out_of_vocab_keys():
    vocab = {"housing", "data-centers"}
    assert validate_topic_keys(["housing", "made-up", "data-centers"], vocab) == ["housing", "data-centers"]


def test_validate_dedupes_and_preserves_order():
    vocab = {"housing", "transit"}
    assert validate_topic_keys(["transit", "housing", "transit"], vocab) == ["transit", "housing"]


def test_validate_empty_when_none_match():
    assert validate_topic_keys(["nope"], {"housing"}) == []


def test_build_prompt_includes_keys_and_section_titles():
    vocab = [
        {"topic_key": "housing", "short_title": "Housing", "question_text": "Rent control?"},
    ]
    sections = [(1, _section("discussion", title="Affordable Housing Ordinance"))]
    prompt = build_classification_prompt(sections, vocab)
    assert "housing" in prompt
    assert "Affordable Housing Ordinance" in prompt
    assert "section 1" in prompt or "1" in prompt
