import json

from src.models import SectionTopic, SummarySection
from src import config
from src.topics import substantive_sections, validate_topic_keys, build_classification_prompt, classify_sections


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


def test_substantive_sections_includes_interview_topic_type():
    # Interviews (news_clip/press_conference) produce section_type="topic".
    # These are substantive and must be eligible for topic classification,
    # otherwise every interview comes out "untagged".
    sections = [
        _section("topic", title="Homelessness Crisis and Solutions"),
        _section("topic", title="Housing Development"),
    ]
    result = substantive_sections(sections)
    assert [i for i, _ in result] == [0, 1]


def test_validate_drops_out_of_vocab_keys():
    vocab = {"housing", "data-centers"}
    assert validate_topic_keys(["housing", "made-up", "data-centers"], vocab) == ["housing", "data-centers"]


def test_validate_dedupes_and_preserves_order():
    vocab = {"housing", "transit"}
    assert validate_topic_keys(["transit", "housing", "transit"], vocab) == ["transit", "housing"]


def test_validate_empty_when_none_match():
    assert validate_topic_keys(["nope"], {"housing"}) == []


# --- classify_sections helpers ---

class _FakeMessage:
    def __init__(self, text):
        self.content = [type("C", (), {"text": text})()]


class _FakeClient:
    def __init__(self, text):
        self._text = text
        self.messages = self

    def create(self, **kwargs):
        return _FakeMessage(self._text)


def _vocab(*keys):
    return [{"topic_key": k, "short_title": k, "question_text": ""} for k in keys]


def _sub_section(title="T"):
    stype = config.SUBSTANTIVE_SECTION_TYPES[0]
    return _section(stype, title=title, idx_text="some body text")


# --- classify_sections tests ---

def test_classify_sections_well_formed_json():
    sections = [_section("opening"), _sub_section("Zoning"), _sub_section("Budget")]
    # sections[1] idx=1, sections[2] idx=2
    response = json.dumps({"sections": [
        {"section_index": 1, "topic_keys": ["housing"]},
        {"section_index": 2, "topic_keys": ["transit"]},
    ]})
    client = _FakeClient(response)
    result = classify_sections(client, sections, _vocab("housing", "transit"))

    assert len(result) == 2
    assert isinstance(result[0], SectionTopic)
    assert result[0].section_index == 1
    assert result[0].topic_keys == ["housing"]
    assert result[1].section_index == 2
    assert result[1].topic_keys == ["transit"]


def test_classify_sections_malformed_json_returns_empty_keys():
    sections = [_sub_section("A"), _sub_section("B")]
    client = _FakeClient("this is definitely not json {{{{")
    result = classify_sections(client, sections, _vocab("housing"))

    assert len(result) == 2
    for item in result:
        assert item.topic_keys == []


def test_classify_sections_section_item_not_a_dict():
    sections = [_sub_section("X")]
    # The "sections" list contains a non-dict item; should be ignored, section gets empty keys
    response = json.dumps({"sections": ["not-a-dict", 42]})
    client = _FakeClient(response)
    result = classify_sections(client, sections, _vocab("housing"))

    assert len(result) == 1
    assert result[0].topic_keys == []


def test_classify_sections_topic_keys_not_a_list():
    sections = [_sub_section("Y")]
    stype = config.SUBSTANTIVE_SECTION_TYPES[0]
    # section_index=0 is the only substantive section
    response = json.dumps({"sections": [
        {"section_index": 0, "topic_keys": "housing"},   # string, not list
    ]})
    client = _FakeClient(response)
    result = classify_sections(client, sections, _vocab("housing"))

    assert len(result) == 1
    assert result[0].topic_keys == []


def test_classify_sections_topic_keys_null():
    sections = [_sub_section("Z")]
    response = json.dumps({"sections": [
        {"section_index": 0, "topic_keys": None},
    ]})
    client = _FakeClient(response)
    result = classify_sections(client, sections, _vocab("housing"))

    assert len(result) == 1
    assert result[0].topic_keys == []


def test_classify_sections_unknown_section_index_ignored():
    sections = [_sub_section("Only")]
    # Model returns section_index=99, which doesn't correspond to any substantive section
    response = json.dumps({"sections": [
        {"section_index": 99, "topic_keys": ["housing"]},
    ]})
    client = _FakeClient(response)
    result = classify_sections(client, sections, _vocab("housing"))

    assert len(result) == 1
    assert result[0].topic_keys == []


def test_classify_sections_out_of_vocab_keys_dropped():
    sections = [_sub_section("Policy")]
    response = json.dumps({"sections": [
        {"section_index": 0, "topic_keys": ["housing", "made-up-key"]},
    ]})
    client = _FakeClient(response)
    result = classify_sections(client, sections, _vocab("housing"))

    assert len(result) == 1
    assert result[0].topic_keys == ["housing"]


def test_classify_sections_no_substantive_sections_returns_empty():
    sections = [_section("opening"), _section("roll_call")]
    client = _FakeClient(json.dumps({"sections": []}))
    result = classify_sections(client, sections, _vocab("housing"))

    assert result == []


def test_build_prompt_includes_keys_and_section_titles():
    vocab = [
        {"topic_key": "housing", "short_title": "Housing", "question_text": "Rent control?"},
    ]
    sections = [(1, _section("discussion", title="Affordable Housing Ordinance"))]
    prompt = build_classification_prompt(sections, vocab)
    assert "housing" in prompt
    assert "Affordable Housing Ordinance" in prompt
    assert "section 1" in prompt or "1" in prompt
