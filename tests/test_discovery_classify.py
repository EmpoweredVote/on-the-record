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


def test_build_prompt_page_kind_youtube_vs_web():
    yt_item = RawItem(url="https://www.youtube.com/watch?v=abc12345678",
                      title="t", description="d", channel_name="KXAN")
    web_item = RawItem(url="https://www.kctv5.com/2026/08/01/governor-debate/",
                       title="t", description="d", channel_name="KCTV5")
    yt_prompt = classify.build_prompt(yt_item, race_label="r", roster_names=[])
    web_prompt = classify.build_prompt(web_item, race_label="r", roster_names=[])
    assert "page_kind: YouTube video" in yt_prompt
    assert "page_kind: web page" in web_prompt


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


def test_vtt_to_text_reconstructs_rolling_auto_captions():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:01.000
today I want to

00:00:01.000 --> 00:00:02.000
today I want to talk about property taxes

00:00:02.000 --> 00:00:03.000
today I want to talk about property taxes and how we fund schools

00:00:03.000 --> 00:00:04.000
and how we fund schools"""
    text = classify.vtt_to_text(vtt)
    assert text == "today I want to talk about property taxes and how we fund schools"


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
                               roster_names=["Maria Delgado"], peek_fetcher=None)
    assert v.confidence == 0.9 and len(provider.prompts) == 1
    assert "Maria Delgado" in provider.prompts[0]
    assert provider.systems[0] == classify._SYSTEM


# VTT-shaped: a cue-timing line and a bare-digit cue-index line. vtt_to_text
# would strip both and dedupe -- the plain-text contract requires this to
# arrive at the model completely unmangled, since peek_fetcher already did
# any VTT-stripping (or there was none to do, for an article excerpt).
_VTT_SHAPED_EXCERPT = ("12\n00:00:01.000 --> 00:00:02.000\n"
                      "you have sixty seconds Senator my question is")


def test_classify_item_mid_confidence_triggers_captions_second_pass():
    provider = _FakeProvider([
        '{"relevant": true, "confidence": 0.5, "why": "unsure"}',
        '{"relevant": true, "confidence": 0.92, "why": "sustained first-person speech"}',
    ])
    fetched = {}

    def fake_peek(url):
        fetched["url"] = url
        return _VTT_SHAPED_EXCERPT

    v = classify.classify_item(provider, _item(), race_label="TX Senate",
                               roster_names=["Maria Delgado"], peek_fetcher=fake_peek)
    assert v.confidence == 0.92 and len(provider.prompts) == 2
    assert _VTT_SHAPED_EXCERPT in provider.prompts[1]
    assert fetched["url"] == _item().url


def test_classify_item_drops_candidates_not_in_roster():
    provider = _FakeProvider(['{"relevant": true, "confidence": 0.9, '
                              '"candidates_present": ["Maria Delgado", "Totally Fake Person"], '
                              '"why": "clear"}'])
    v = classify.classify_item(provider, _item(), race_label="TX Senate",
                               roster_names=["Maria Delgado"], peek_fetcher=None)
    assert v.candidates_present == ["Maria Delgado"]


def test_page_peek_second_pass_uses_plain_excerpt_verbatim():
    prompts = []

    class _P:
        def complete(self, prompt, *, max_tokens, temperature, system=None):
            prompts.append(prompt)
            reply_conf = 0.5 if len(prompts) == 1 else 0.9
            return ('{"relevant": true, "confidence": %s,'
                    ' "candidates_present": [], "event_kind": "debate",'
                    ' "source_tier": 1, "original_vs_clip": "original",'
                    ' "route": "ingest", "why": "w"}' % reply_conf)

    item = RawItem(url="https://www.kctv5.com/2026/08/01/governor-debate/",
                   title="t", description="d", channel_name="KCTV5")
    verdict = classify.classify_item(_P(), item, race_label="KS Governor",
                                     roster_names=["Alice Example"],
                                     peek_fetcher=lambda url: _VTT_SHAPED_EXCERPT)
    assert verdict.confidence == 0.9
    assert _VTT_SHAPED_EXCERPT in prompts[1]


def test_parse_verdict_accepts_questionnaire_kind():
    v = classify.parse_verdict(
        '{"relevant": true, "confidence": 0.8, "event_kind": "questionnaire",'
        ' "source_tier": 2, "route": "quote_source", "why": "unedited answers"}')
    assert v.event_kind_guess == "questionnaire"
    assert v.source_tier_guess == 2
    assert v.route == "quote_source"


def test_build_prompt_tiers_by_questioner_independence():
    prompt = classify.build_prompt(_item(), race_label="TX Senate",
                                   roster_names=["Maria Delgado"])
    # town halls are tier 1, not tier 3
    assert "town hall (independent moderator, opponents, or citizen questioning)" in prompt
    # prepared remarks live in tier 3 alongside sympathetic-questioner interviews
    assert "stump speech" in prompt.split("4 =")[0].split("3 =")[1]
    # questionnaire is an emittable kind
    assert "questionnaire" in prompt
