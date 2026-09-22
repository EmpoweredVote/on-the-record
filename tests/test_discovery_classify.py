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


# A Ballotpedia Candidate Connection page (and questionnaire pages generally)
# reads as third-person election boilerplate in its title/search snippet -- the
# candidate's OWN answers live deep in the page body, which only the page peek
# surfaces. So a metadata-only reject of a WEB PAGE is unreliable for the
# "candidate's own words" test and must still trigger the peek + re-classify,
# even when the first pass was confident (out of the mid band). Regression for
# the Slice-2 Phase-4a finding: completed Ballotpedia CC pages were retrieved
# and the peek carried the answers, but classify_item never fetched the peek.
_CC_EXCERPT = ("completed Ballotpedia's Candidate Connection survey in 2026. "
               "Who are you? Tell us about yourself. I am running to ...")


def test_classify_item_confident_reject_of_web_page_still_peeks():
    provider = _FakeProvider([
        '{"relevant": false, "confidence": 0.9, "why": "third-person page"}',
        '{"relevant": true, "confidence": 0.9, "event_kind": "questionnaire",'
        ' "original_vs_clip": "original", "why": "candidate own answers"}',
    ])
    fetched = {}

    def fake_peek(url):
        fetched["url"] = url
        return _CC_EXCERPT

    item = RawItem(url="https://ballotpedia.org/Karen_Bass",
                   title="Karen Bass - Ballotpedia",
                   description="Incumbent Karen Bass and Nithya Raman are running ...",
                   via="hub")
    v = classify.classify_item(provider, item, race_label="Los Angeles Mayor (CA, 2026)",
                               roster_names=["Karen Bass", "Nithya Raman"],
                               peek_fetcher=fake_peek)
    assert fetched.get("url") == item.url          # the peek WAS fetched
    assert len(provider.prompts) == 2              # a second (peek) pass ran
    assert _CC_EXCERPT in provider.prompts[1]
    assert v.relevant is True and v.event_kind_guess == "questionnaire"


def test_classify_item_confident_reject_of_youtube_does_not_peek():
    """The re-peek-on-reject widening is web-page-only: a confident YouTube
    reject keeps its single-pass behavior (the captions peek stays mid-band
    gated), so the caption-lane cost model is unchanged."""
    provider = _FakeProvider(['{"relevant": false, "confidence": 0.9, "why": "clip"}'])
    called = {"n": 0}

    def fake_peek(url):
        called["n"] += 1
        return _VTT_SHAPED_EXCERPT

    v = classify.classify_item(provider, _item(), race_label="TX Senate",
                               roster_names=["Maria Delgado"], peek_fetcher=fake_peek)
    assert called["n"] == 0 and len(provider.prompts) == 1 and v.relevant is False


def test_classify_item_confident_accept_of_web_page_does_not_peek():
    """A confident first-pass ACCEPT already stands -- no peek, no extra LLM
    call -- so widening the reject path adds cost only for rejected web pages."""
    provider = _FakeProvider(['{"relevant": true, "confidence": 0.9, "why": "clear"}'])
    called = {"n": 0}

    def fake_peek(url):
        called["n"] += 1
        return _CC_EXCERPT

    item = RawItem(url="https://ballotpedia.org/Karen_Bass", title="t",
                   description="d", via="hub")
    v = classify.classify_item(provider, item, race_label="Los Angeles Mayor (CA, 2026)",
                               roster_names=["Karen Bass"], peek_fetcher=fake_peek)
    assert called["n"] == 0 and len(provider.prompts) == 1 and v.relevant is True


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
    # rung 4 must be present so its absence can't silently pass the split below
    assert "4 =" in prompt
    # prepared remarks live in tier 3 alongside sympathetic-questioner interviews
    assert "stump speech" in prompt.split("4 =")[0].split("3 =")[1]
    # questionnaire is an emittable kind (pins the JSON enum, not just the tier sentence)
    assert "community_meeting|questionnaire|other" in prompt


def test_questionnaire_is_a_known_event_kind():
    from src.event_kinds import EVENT_KINDS
    assert "questionnaire" in EVENT_KINDS


def test_parse_verdict_keeps_questionnaire_kind():
    from src.discovery.classify import parse_verdict
    v = parse_verdict('{"relevant": true, "confidence": 0.8, "candidates_present": [],'
                      ' "event_kind": "questionnaire", "source_tier": 2,'
                      ' "original_vs_clip": "original", "route": "quote_source", "why": "x"}')
    assert v.event_kind_guess == "questionnaire"
    assert v.route == "quote_source"


def test_prompt_has_current_cycle_check():
    from src.discovery.classify import build_prompt
    from src.discovery.models import RawItem
    item = RawItem(url="https://ballotpedia.org/x", title="2022 debate", description="prior cycle")
    prompt = build_prompt(item, race_label="AZ · U.S. Senate · General · 2026", roster_names=["A", "B"])
    low = prompt.lower()
    assert "current" in low and ("cycle" in low or "prior" in low)
    # The current-cycle instruction itself must name THIS race (keyed on
    # race_label) — assert the binding phrase from the instruction, not merely
    # that race_label appears somewhere (it is also echoed in the "Race:" line,
    # so a bare containment check would pass even if the instruction dropped it).
    assert "the tracked race is AZ · U.S. Senate · General · 2026" in prompt


def test_prompt_prior_cycle_only_when_strictly_earlier_and_guards_wrong_contest():
    """Increment-3 tightening (eval-driven): prior_cycle is set ONLY when the
    content's cycle year is EARLIER than the race's (same year -> current), and
    the wrong-contest guard holds even when the page carries a candidate's own
    words but they aren't in this race's roster."""
    from src.discovery.classify import build_prompt
    from src.discovery.models import RawItem
    item = RawItem(url="https://ballotpedia.org/Jane_Doe", title="Jane Doe", description="")
    prompt = build_prompt(item, race_label="CA · Mayor · 2026", roster_names=["Jane Doe"])
    low = " ".join(prompt.lower().split())   # whitespace-normalized so line wraps don't hide phrases
    assert "earlier" in low            # prior_cycle only when content year < race year
    assert "same year" in low          # equal years -> current (prior_cycle false)
    assert "not in the tracked candidates" in low   # wrong-contest guard keyed on roster
    assert "different race" in low                   # own words about another race != this race's source


def test_parse_verdict_reads_prior_cycle_flag():
    """A tracked candidate's OWN prior-cycle answers are FLAGGED, not rejected:
    relevant stays true, prior_cycle true, and the cycle year is carried."""
    v = classify.parse_verdict(
        '{"relevant": true, "confidence": 0.85, "candidates_present": ["Jane Doe"],'
        ' "event_kind": "questionnaire", "source_tier": 2, "original_vs_clip": "original",'
        ' "route": "quote_source", "prior_cycle": true, "source_cycle_year": "2020",'
        ' "why": "candidate\'s own 2020 Candidate Connection answers"}')
    assert v.relevant is True
    assert v.prior_cycle is True
    assert v.source_cycle_year == "2020"


def test_parse_verdict_prior_cycle_defaults_false_when_absent():
    """Back-compat: replies without the new keys land prior_cycle False /
    source_cycle_year None (existing fixtures/callers keep working)."""
    v = classify.parse_verdict(
        '{"relevant": true, "confidence": 0.9, "candidates_present": [],'
        ' "event_kind": "debate", "source_tier": 1, "original_vs_clip": "original",'
        ' "route": "ingest", "why": "current debate"}')
    assert v.prior_cycle is False
    assert v.source_cycle_year is None


def test_parse_verdict_coerces_source_cycle_year_int_to_str():
    v = classify.parse_verdict(
        '{"relevant": true, "confidence": 0.8, "prior_cycle": true, "source_cycle_year": 2018,'
        ' "why": "x"}')
    assert v.source_cycle_year == "2018"


def test_prompt_distinguishes_wrong_contest_from_own_prior_answers():
    """The current-cycle instruction must GUARD a wrong-contest page but FLAG a
    tracked candidate's own prior-cycle answers, and expose the new JSON keys."""
    from src.discovery.classify import build_prompt
    from src.discovery.models import RawItem
    item = RawItem(url="https://ballotpedia.org/Jane_Doe", title="Jane Doe", description="")
    prompt = build_prompt(item, race_label="CA · Mayor · 2026", roster_names=["Jane Doe"])
    low = prompt.lower()
    assert "prior_cycle" in low                     # the flag is in the JSON schema
    assert "source_cycle_year" in low               # and the cycle year field
    assert "wrong contest" in low or "different election" in low  # the guard case
    assert "own" in low and "prior" in low          # the flag case (own prior-cycle answers)
