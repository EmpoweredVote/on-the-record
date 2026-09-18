from src.discovery.hubs import Hub
from src.discovery.hub_search import raw_items_for_race


class _FakeTavily:
    """Records every query it was called with and returns canned results
    keyed by a substring match against the query (falls back to a default
    result list otherwise)."""

    def __init__(self, results_by_query_substring=None, default=None):
        self.calls = []
        self.results_by_query_substring = results_by_query_substring or {}
        self.default = default if default is not None else [
            {"title": "Default", "url": "https://example.com/x", "content": "c"},
        ]

    def __call__(self, query, *args, **kwargs):
        self.calls.append(query)
        for substring, results in self.results_by_query_substring.items():
            if substring in query:
                return results
        return self.default


def _ballotpedia_hub():
    return Hub(name="Ballotpedia", scope="global", poll_method="scoped_search",
               domain="ballotpedia.org")


def _template_hub():
    return Hub(name="AZ Voter Guide", scope="state", state="AZ",
               poll_method="scoped_search",
               query_template="<locality> voter guide <year> candidates")


def _vote411_hub():
    return Hub(name="VOTE411", scope="global", poll_method="scoped_search",
               domain="vote411.org", tos_bucket="vote411-lwv")


def test_domain_hub_produces_site_scoped_query_with_candidate_names(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    raw_items_for_race(
        [_ballotpedia_hub()],
        candidates=["Jane Smith", "John Doe"],
        locality="Springfield",
        year="2026",
    )

    assert len(fake.calls) == 1
    query = fake.calls[0]
    assert query.startswith("site:ballotpedia.org ")
    assert "Jane Smith" in query
    assert "John Doe" in query


def test_template_hub_fills_locality_and_year(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    raw_items_for_race(
        [_template_hub()],
        candidates=["Jane Smith"],
        locality="Springfield",
        year="2026",
    )

    assert fake.calls == ["Springfield voter guide 2026 candidates"]


def test_returned_items_carry_via_hub_and_result_fields(monkeypatch):
    fake = _FakeTavily(default=[
        {"title": "T1", "url": "https://ballotpedia.org/page1", "content": "d1"},
    ])
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    items = raw_items_for_race(
        [_ballotpedia_hub()],
        candidates=["Jane Smith"],
        locality="Springfield",
        year="2026",
    )

    assert len(items) == 1
    item = items[0]
    assert item.via == "hub"
    assert item.url == "https://ballotpedia.org/page1"
    assert item.title == "T1"
    assert item.description == "d1"


def test_pointer_only_hub_is_skipped_no_search_no_items(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    items = raw_items_for_race(
        [_vote411_hub()],
        candidates=["Jane Smith"],
        locality="Springfield",
        year="2026",
    )

    assert fake.calls == []
    assert items == []


def test_pointer_only_hub_detected_via_notes_case_insensitive(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    hub = Hub(name="Some Pointer Hub", scope="global", poll_method="scoped_search",
              domain="example.org", notes="This is a Pointer-Only source, no scraping.")

    items = raw_items_for_race(
        [hub],
        candidates=["Jane Smith"],
        locality="Springfield",
        year="2026",
    )

    assert fake.calls == []
    assert items == []


def test_budget_caps_number_of_searches_and_pointer_only_does_not_count(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    hubs = [
        _vote411_hub(),  # pointer-only, should not consume budget
        Hub(name="Hub A", scope="global", poll_method="scoped_search", domain="a.com"),
        Hub(name="Hub B", scope="global", poll_method="scoped_search", domain="b.com"),
        Hub(name="Hub C", scope="global", poll_method="scoped_search", domain="c.com"),
    ]

    raw_items_for_race(
        hubs,
        candidates=["Jane Smith"],
        locality="Springfield",
        year="2026",
        budget=2,
    )

    assert len(fake.calls) == 2


def test_domain_filtering_drops_off_domain_results_and_caps_at_two(monkeypatch):
    fake = _FakeTavily(default=[
        {"title": "On1", "url": "https://ballotpedia.org/a", "content": "c1"},
        {"title": "Off", "url": "https://example.com/off", "content": "c2"},
        {"title": "On2", "url": "https://ballotpedia.org/b", "content": "c3"},
        {"title": "On3", "url": "https://ballotpedia.org/c", "content": "c4"},
    ])
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    items = raw_items_for_race(
        [_ballotpedia_hub()],
        candidates=["Jane Smith"],
        locality="Springfield",
        year="2026",
    )

    urls = [item.url for item in items]
    assert "https://example.com/off" not in urls
    assert len(items) == 2
    assert all("ballotpedia.org" in u for u in urls)


def test_hub_with_no_domain_and_no_template_is_skipped(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    hub = Hub(name="Nothing to search", scope="global", poll_method="scoped_search")

    items = raw_items_for_race(
        [hub],
        candidates=["Jane Smith"],
        locality="Springfield",
        year="2026",
    )

    assert fake.calls == []
    assert items == []


def test_domain_hub_falls_back_to_locality_when_no_candidates(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    raw_items_for_race(
        [_ballotpedia_hub()],
        candidates=[],
        locality="Springfield",
        year="2026",
    )

    assert fake.calls == ["site:ballotpedia.org Springfield 2026"]


def test_local_type_sub_cap_limits_only_local_type_searches(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    hubs = [
        Hub(name="LT1", scope="local_type", poll_method="scoped_search",
            query_template="<locality> forum <year>"),
        Hub(name="LT2", scope="local_type", poll_method="scoped_search",
            query_template="<locality> voter guide <year>"),
        Hub(name="LT3", scope="local_type", poll_method="scoped_search",
            query_template="<locality> chamber <year>"),
        Hub(name="Domain", scope="global", poll_method="scoped_search", domain="a.com"),
    ]
    raw_items_for_race(hubs, candidates=["Jane"], locality="Springfield",
                       year="2026", budget=6, local_type_budget=1)

    # exactly ONE local_type search + the domain search = 2 total
    assert len(fake.calls) == 2
    assert any(c.startswith("site:a.com") for c in fake.calls)
    assert sum(1 for c in fake.calls if "Springfield" in c and "site:" not in c) == 1


def test_local_type_sub_cap_does_not_gate_state_scope_template_hubs(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    hubs = [
        Hub(name="LT1", scope="local_type", poll_method="scoped_search",
            query_template="<locality> forum <year>"),
        Hub(name="LT2", scope="local_type", poll_method="scoped_search",
            query_template="<locality> chamber <year>"),
        Hub(name="AZ Voter Guide", scope="state", poll_method="scoped_search",
            query_template="<locality> voter guide <year>"),
    ]
    raw_items_for_race(hubs, candidates=["Jane"], locality="Springfield",
                       year="2026", budget=6, local_type_budget=1)

    # The sub-cap keys on hub.scope == "local_type", not on "no domain" -- a
    # state-scope template hub (also domain-less) must run its search
    # regardless of how many local_type searches already used up the sub-cap.
    assert "Springfield voter guide 2026" in fake.calls
    # ... while only ONE of the two local_type hubs actually searched.
    local_type_calls = [c for c in fake.calls if c in
                        ("Springfield forum 2026", "Springfield chamber 2026")]
    assert len(local_type_calls) == 1
    assert len(fake.calls) == 2  # one local_type search + the state hub search


def test_local_type_budget_none_means_no_sub_cap(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    hubs = [
        Hub(name="LT1", scope="local_type", poll_method="scoped_search",
            query_template="<locality> a <year>"),
        Hub(name="LT2", scope="local_type", poll_method="scoped_search",
            query_template="<locality> b <year>"),
    ]
    raw_items_for_race(hubs, candidates=["Jane"], locality="Springfield",
                       year="2026", budget=6)  # local_type_budget defaults None
    assert len(fake.calls) == 2
