from src.discovery import classify
from src.discovery.models import RawItem, Verdict


def test_parse_verdict_happy_path():
    v = classify.parse_verdict("""Here you go:
    {"relevant": true, "confidence": 0.85,
     "candidates_present": ["Maria Delgado"],
     "event_kind": "debate", "source_tier": 1,
     "original_vs_clip": "original", "route": "ingest",
     "why": "58-min video, all candidates in description"}""")
    assert v.relevant and v.confidence == 0.85
    assert v.event_kind_guess == "debate" and v.source_tier_guess == 1
    assert v.rejected_reason is None


def test_parse_verdict_no_json():
    v = classify.parse_verdict("I cannot help with that.")
    assert not v.relevant and v.rejected_reason == "no JSON in reply"


def test_parse_verdict_malformed_json():
    v = classify.parse_verdict('{"relevant": true, "confidence": }')
    assert v.rejected_reason == "malformed JSON"


def test_parse_verdict_clamps_and_validates():
    v = classify.parse_verdict('{"relevant": true, "confidence": 7, "event_kind": "circus", "route": "banana"}')
    assert v.confidence == 1.0
    assert v.event_kind_guess is None   # unknown kind dropped
    assert v.route == "ingest"          # unknown route falls back


def test_vtt_to_text_strips_cues_and_dedupes():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
I will cut <b>property taxes</b>

00:00:02.000 --> 00:00:04.000
I will cut property taxes

00:00:04.000 --> 00:00:06.000
starting with my first budget."""
    text = classify.vtt_to_text(vtt)
    assert text == "I will cut property taxes starting with my first budget."


class _FakeProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.systems = []

    def complete(self, prompt, *, max_tokens, temperature, system=None):
        self.prompts.append(prompt)
        self.systems.append(system)
        return self.replies.pop(0)


def _item():
    return RawItem(url="https://www.youtube.com/watch?v=abc12345678",
                   title="Full debate", description="All four candidates",
                   channel_name="KXAN", duration_seconds=3300, via="search")


def test_classify_item_single_pass_when_confident():
    provider = _FakeProvider(['{"relevant": true, "confidence": 0.9, "why": "clear"}'])
    v = classify.classify_item(provider, _item(), race_label="TX Senate",
                               roster_names=["Maria Delgado"], captions_fetcher=None)
    assert v.confidence == 0.9 and len(provider.prompts) == 1
    assert "Maria Delgado" in provider.prompts[0]
    assert provider.systems[0] == classify._SYSTEM


def test_classify_item_mid_confidence_triggers_captions_second_pass():
    provider = _FakeProvider([
        '{"relevant": true, "confidence": 0.5, "why": "unsure"}',
        '{"relevant": true, "confidence": 0.92, "why": "sustained first-person speech"}',
    ])
    fetched = {}

    def fake_captions(url):
        fetched["url"] = url
        return "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nI will cut taxes"

    v = classify.classify_item(provider, _item(), race_label="TX Senate",
                               roster_names=["Maria Delgado"], captions_fetcher=fake_captions)
    assert v.confidence == 0.92 and len(provider.prompts) == 2
    assert "I will cut taxes" in provider.prompts[1]
    assert fetched["url"] == _item().url
