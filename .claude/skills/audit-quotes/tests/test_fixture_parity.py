import json, pathlib
from scripts.checks import QUOTE_CHECKS

FIX = pathlib.Path(__file__).resolve().parents[4] / "docs/quote-curation/fixtures/mechanical-checks.json"

def _ids(row):
    out = set()
    for chk in QUOTE_CHECKS:
        f = chk(row)
        if f: out.add(f.check_id)
    return out

def test_every_case_matches_checks_py():
    cases = json.loads(FIX.read_text())
    for c in cases:
        assert _ids(c["row"]) == set(c["expect"]), f"{c['name']}: {_ids(c['row'])} != {set(c['expect'])}"

def test_fixtures_cover_every_quote_check():
    cases = json.loads(FIX.read_text())
    seen = {cid for c in cases for cid in c["expect"]}
    required = {"note-missing","note-section-ref","note-too-long","deid-missing","trailing-ellipsis",
                "partisan-tell","invalid-source","unquotable-source","scorecard-source",
                "pointer-only-source","stance-label"}
    assert required <= seen, f"fixtures miss: {required - seen}"
    # `required` is a hand-kept list, so also demand that every function in QUOTE_CHECKS fires on
    # at least one case: a check added to QUOTE_CHECKS without a fixture fails here, not silently.
    idle = [chk.__name__ for chk in QUOTE_CHECKS if not any(chk(c["row"]) for c in cases)]
    assert not idle, f"no fixture case exercises: {idle}"
