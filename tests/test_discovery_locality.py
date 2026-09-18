from src.discovery.locality import local_query_locality


def test_federal_race_returns_none():
    assert local_query_locality(
        "U.S. Representative District 9", "United States Federal Government") is None


def test_statewide_state_of_prefix_returns_none():
    assert local_query_locality("Governor", "State of Arizona") is None


def test_statewide_null_government_returns_none():
    assert local_query_locality("Governor of Maine", None) is None


def test_local_place_is_cleaned():
    assert local_query_locality(
        "Los Angeles Mayor", "Los Angeles, California, US") == "Los Angeles"


def test_county_place_is_cleaned():
    assert local_query_locality(
        "Brown County Commissioner", "Brown County, Indiana, US") == "Brown County"


def test_local_bare_place_without_tail_is_unchanged():
    assert local_query_locality("Bloomington School Board", "Bloomington") == "Bloomington"


def test_local_level_but_no_government_returns_none():
    assert local_query_locality("Los Angeles Mayor", None) is None


def test_local_level_but_federal_sentinel_government_returns_none():
    # belt-and-suspenders: the sentinel wins even if the level looks local
    assert local_query_locality("Los Angeles Mayor", "United States Federal Government") is None
