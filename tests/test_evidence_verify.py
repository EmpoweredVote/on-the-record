from src.evidence.verify import normalize, verbatim_ok

SRC = ("Mayor Bass said, “We will build 30,000 units of housing, "
       "and we will do it with union labor,” during the forum.")

def test_exact_substring_passes():
    assert verbatim_ok("We will build 30,000 units of housing", SRC)

def test_curly_and_straight_quotes_normalize_equal():
    assert verbatim_ok('"We will build 30,000 units of housing"', SRC)

def test_ellipsis_tolerant_runs_must_be_in_order():
    assert verbatim_ok("We will build 30,000 units of housing … with union labor", SRC)
    assert not verbatim_ok("with union labor … We will build 30,000 units", SRC)

def test_non_substring_fails():
    assert not verbatim_ok("We will build affordable homes for everyone", SRC)
