import json
from src.evidence.extract import parse_extract, extract_quotes, build_extract_prompt, chunk_text

class FakeProvider:
    def __init__(self, responses): self._r = list(responses); self.prompts = []
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        self.prompts.append(prompt); return self._r.pop(0)

PAYLOAD = json.dumps({"quotes": [
    {"text": "We will build 30,000 units of housing.",
     "context": "At the forum, she said: We will build 30,000 units of housing.",
     "issue": "housing", "date": "2026-05-01", "setting": "candidate forum",
     "is_own_words": True, "is_primary_venue": True,
     "reported_event": None, "primary_handle": None}]})

def test_parse_extract_reads_fenced_json():
    out = parse_extract("```json\n" + PAYLOAD + "\n```")
    assert len(out) == 1 and out[0].issue == "housing"
    assert out[0].text.startswith("We will build")

def test_extract_quotes_calls_provider_and_parses():
    p = FakeProvider([PAYLOAD])
    out = extract_quotes("...source text...", candidate_name="Karen Bass", provider=p)
    assert out[0].is_primary_venue is True
    assert "Karen Bass" in p.prompts[0]

def test_parse_extract_returns_empty_on_malformed_shapes():
    assert parse_extract("[]") == []
    assert parse_extract("null") == []
    assert parse_extract("not json at all") == []
    assert parse_extract('{"quotes": ["just a string", 123]}') == []

def test_parse_extract_skips_non_string_text_without_raising():
    payload = json.dumps({"quotes": [
        {"text": 42, "context": "junk", "issue": "housing"},
        {"text": "Real verbatim line.", "context": "ctx", "issue": "housing"}]})
    out = parse_extract(payload)
    assert len(out) == 1
    assert out[0].text == "Real verbatim line."

def test_extract_quotes_passes_zero_temperature_and_system():
    class RecordingProvider:
        def __init__(self, resp): self.resp = resp; self.kw = None
        def complete(self, prompt, *, max_tokens, temperature, system=None):
            self.kw = {"max_tokens": max_tokens, "temperature": temperature, "system": system}
            return self.resp
    p = RecordingProvider(PAYLOAD)
    extract_quotes("...source...", candidate_name="X", provider=p)
    assert p.kw["temperature"] == 0.0 and p.kw["system"]

def test_build_extract_prompt_instructs_multi_sentence_mechanism_inclusive_extraction():
    prompt = build_extract_prompt("Some source text.", "Alice")
    assert "CONTIGUOUS" in prompt
    assert "mechanism" in prompt
    assert "1 to 3 sentences" in prompt
    assert "HOW" in prompt
    assert "Alice" in prompt

def test_parse_extract_handles_multi_sentence_quote_text():
    multi_sentence_payload = json.dumps({"quotes": [
        {"text": "We must build much more housing to reduce housing costs. This includes housing at all income levels, deed-restricted affordable, market-rate, social housing, and homeless shelters.",
         "context": "At the forum, she said: We must build much more housing…",
         "issue": "housing", "date": "2026-05-01", "setting": "candidate forum",
         "is_own_words": True, "is_primary_venue": True,
         "reported_event": None, "primary_handle": None}]})
    out = parse_extract(multi_sentence_payload)
    assert len(out) == 1
    assert out[0].issue == "housing"
    assert "much more housing" in out[0].text
    assert "deed-restricted affordable" in out[0].text
    assert "homeless shelters" in out[0].text


def test_chunk_text_short_returns_single_window():
    assert chunk_text("hello", size=100) == ["hello"]
    assert chunk_text("", size=100) == []

def test_chunk_text_windows_cover_all_text_with_overlap():
    text = "".join(f"word{i} " for i in range(4000))  # ~ >12000 chars
    windows = chunk_text(text, size=3000, overlap=500)
    assert len(windows) > 1
    assert all(len(w) <= 3000 for w in windows)
    # every character position appears in at least one window (no gaps)
    covered = 0
    for w in windows:
        start = text.index(w, max(0, covered - len(w)))
        assert start <= covered  # windows are contiguous/overlapping, no gap
        covered = max(covered, start + len(w))
    assert covered == len(text)

def test_chunk_text_prefers_paragraph_boundary():
    left = "a" * 2900
    right = "b" * 2900
    text = left + "\n\n" + right
    windows = chunk_text(text, size=3000, overlap=200)
    # the first window ends at the blank-line boundary, not mid-run
    assert windows[0].endswith("\n\n") or windows[0] == left + "\n\n"


def test_parse_extract_normal_json():
    raw = '{"quotes":[{"text":"I will build 10000 homes.","issue":"housing"}]}'
    out = parse_extract(raw)
    assert len(out) == 1 and out[0].text == "I will build 10000 homes."

def test_parse_extract_salvages_truncated_reply():
    # Two complete objects, then a third cut off mid-string (the Bass failure).
    raw = ('{"quotes":['
           '{"text":"A: declare a state of emergency.","issue":"homelessness"},'
           '{"text":"B: end all street encampments.","issue":"homelessness"},'
           '{"text":"C: appoint and empower one indiv')
    out = parse_extract(raw)
    assert [c.text for c in out] == [
        "A: declare a state of emergency.",
        "B: end all street encampments.",
    ]

def test_parse_extract_junk_returns_empty():
    assert parse_extract("not json at all") == []
    assert parse_extract("") == []
