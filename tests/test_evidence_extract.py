import json
from src.evidence.extract import parse_extract, extract_quotes

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

def test_extract_quotes_passes_zero_temperature_and_system():
    class RecordingProvider:
        def __init__(self, resp): self.resp = resp; self.kw = None
        def complete(self, prompt, *, max_tokens, temperature, system=None):
            self.kw = {"max_tokens": max_tokens, "temperature": temperature, "system": system}
            return self.resp
    p = RecordingProvider(PAYLOAD)
    extract_quotes("...source...", candidate_name="X", provider=p)
    assert p.kw["temperature"] == 0.0 and p.kw["system"]
