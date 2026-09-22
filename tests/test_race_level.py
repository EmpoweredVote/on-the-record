from src.race_level import LEVEL_ORDER, race_level


def test_race_level_classifies_each_level():
    assert race_level("U.S. Representative District 9") == "federal"
    assert race_level("U.S. Senate Alabama") == "federal"
    assert race_level("Governor") == "state"
    assert race_level("Governor of Maine") == "state"
    assert race_level("Attorney General") == "state"
    assert race_level("Brown County Commissioner") == "county"
    assert race_level("Bloomington School Board") == "school"
    assert race_level("Los Angeles Mayor") == "local"
    assert race_level(None) == "local"
    assert LEVEL_ORDER == ("federal", "state", "county", "local", "school")


def test_gui_coverage_reexports_race_level():
    # coverage.py must keep exposing race_level for its existing callers/tests.
    from gui.coverage import race_level as cov_race_level
    assert cov_race_level("Governor") == "state"
