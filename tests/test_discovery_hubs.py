from src.discovery.hubs import Hub, hubs_for_race, load_hubs


class _FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def _mixed_hubs():
    return [
        Hub(name="Ballotpedia", scope="global", poll_method="scoped_search",
            domain="ballotpedia.org"),
        Hub(name="AZ Clean Elections Voter Guide", scope="state", state="AZ",
            poll_method="scoped_search", domain="azcleanelections.gov"),
        Hub(name="OR Voter Guide", scope="state", state="OR",
            poll_method="scoped_search", domain="oregonvotes.gov"),
        Hub(name="City Clerk Local Voter Guides", scope="local_type",
            poll_method="scoped_search"),
        Hub(name="AZ Republic Candidate Q&A Feed", scope="state", state="AZ",
            poll_method="feed", domain="azcentral.com"),
    ]


def test_hubs_for_race_scoped_only_default_includes_global_state_local_excludes_wrong_state_and_feed():
    hubs = _mixed_hubs()
    result = hubs_for_race(hubs, state="AZ")

    names = [h.name for h in result]
    assert names == [
        "Ballotpedia",
        "AZ Clean Elections Voter Guide",
        "City Clerk Local Voter Guides",
    ]
    assert "OR Voter Guide" not in names
    assert "AZ Republic Candidate Q&A Feed" not in names


def test_hubs_for_race_scoped_only_false_includes_feed_hubs_too():
    hubs = _mixed_hubs()
    result = hubs_for_race(hubs, state="AZ", scoped_only=False)

    names = [h.name for h in result]
    assert names == [
        "Ballotpedia",
        "AZ Clean Elections Voter Guide",
        "City Clerk Local Voter Guides",
        "AZ Republic Candidate Q&A Feed",
    ]
    assert "OR Voter Guide" not in names


def test_hubs_for_race_does_not_mutate_input_list():
    hubs = _mixed_hubs()
    original_len = len(hubs)
    hubs_for_race(hubs, state="AZ")
    assert len(hubs) == original_len


def test_hubs_for_race_state_match_is_case_insensitive_on_hub_state():
    hubs = [Hub(name="az hub", scope="state", state="az", poll_method="scoped_search")]
    result = hubs_for_race(hubs, state="AZ")
    assert [h.name for h in result] == ["az hub"]


def test_hubs_for_race_state_match_is_case_insensitive_on_race_state():
    hubs = [Hub(name="AZ hub", scope="state", state="AZ", poll_method="scoped_search")]
    result = hubs_for_race(hubs, state="az")
    assert [h.name for h in result] == ["AZ hub"]


def test_hubs_for_race_none_state_excludes_state_scoped_but_keeps_global_and_local_type():
    hubs = _mixed_hubs()
    result = hubs_for_race(hubs, state=None)

    names = [h.name for h in result]
    assert names == ["Ballotpedia", "City Clerk Local Voter Guides"]


def test_hubs_for_race_empty_state_excludes_state_scoped_too():
    hubs = _mixed_hubs()
    result = hubs_for_race(hubs, state="")

    names = [h.name for h in result]
    assert names == ["Ballotpedia", "City Clerk Local Voter Guides"]


def test_load_hubs_maps_active_rows_to_hub_objects():
    cur = _FakeCursor(rows=[
        ("11111111-1111-1111-1111-111111111111", "Ballotpedia", "global", None,
         "guide", "scoped_search", "ballotpedia.org", None, "green", True, "seed", None),
        ("22222222-2222-2222-2222-222222222222", "AZ Clean Elections Voter Guide",
         "state", "AZ", "guide", "scoped_search", "azcleanelections.gov",
         "{candidate} voter guide", "green", True, "seed", "state PEC guide"),
    ])
    hubs = load_hubs(cur)

    sql, _ = cur.executed[0]
    assert "essentials.source_hubs" in sql
    assert "where active" in sql.lower()
    assert "order by name" in sql.lower()

    assert len(hubs) == 2
    assert hubs[0].id == "11111111-1111-1111-1111-111111111111"
    assert hubs[0].name == "Ballotpedia"
    assert hubs[0].scope == "global"
    assert hubs[0].state is None
    assert hubs[0].poll_method == "scoped_search"
    assert hubs[0].domain == "ballotpedia.org"
    assert hubs[0].active is True

    assert hubs[1].state == "AZ"
    assert hubs[1].query_template == "{candidate} voter guide"
    assert hubs[1].notes == "state PEC guide"
