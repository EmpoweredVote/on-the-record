import pytest
from gui.coverage import race_level

@pytest.mark.parametrize("name,expected", [
    ("U.S. Senate — Indiana", "federal"),
    ("U.S. Representative District 9", "federal"),
    ("President of the United States", "federal"),
    ("Indiana Governor", "state"),
    ("Attorney General", "state"),
    ("State Representative, District 060", "state"),
    ("Monroe County Commissioner, District 3", "county"),
    ("County Council At-Large", "county"),
    ("MCCSC School Board, District 1", "school"),
    ("Bloomington Board of Education", "school"),
    ("Bloomington Mayor", "local"),
    ("Bloomington City Council, District 4", "local"),
    (None, "local"),
])
def test_race_level(name, expected):
    assert race_level(name) == expected
