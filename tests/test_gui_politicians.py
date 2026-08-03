"""Tests for gui.politicians — the speaker-link politician picker.

Mirrors tests/test_gui_races.py: pure label/parse functions tested directly,
the DB query tested through a fake cursor.
"""
import json

from gui import politicians
from gui.politicians import (
    candidacy_display,
    mark_duplicate_names,
    parse_name_query,
    politician_display,
)
from gui.races import race_display


def test_parse_name_query_splits_tokens():
    assert parse_name_query("Thomas Tiffany") == ["thomas", "tiffany"]


def test_parse_name_query_drops_punctuation_and_extra_space():
    assert parse_name_query("  O'Brien,  Mary-Kate ") == ["brien", "mary", "kate"]


def test_parse_name_query_empty():
    assert parse_name_query("") == []
    assert parse_name_query("   ") == []


def test_parse_name_query_drops_a_bare_middle_initial():
    # Every token becomes an AND-ed clause, so keeping "f" would require the
    # stored row to contain an F somewhere: "John F Kennedy" returns ZERO against
    # the stored "John Kennedy" (verified against prod). Dropping 1-char tokens
    # is what makes the query survive an initial the record doesn't carry.
    assert parse_name_query("John F Kennedy") == ["john", "kennedy"]
    assert parse_name_query("Thomas P. Tiffany") == ["thomas", "tiffany"]


def test_parse_name_query_drops_generational_suffixes():
    # name_suffix is its own column and is not searched, so "Wesley Hunt Jr"
    # returns zero against the stored "Wesley Hunt" (verified against prod).
    assert parse_name_query("Wesley Hunt Jr.") == ["wesley", "hunt"]
    assert parse_name_query("Harold Ford III") == ["harold", "ford"]


def test_politician_display_name_only():
    assert politician_display({"full_name": "Mandela Barnes"}) == "Mandela Barnes"


def test_politician_display_name_and_office():
    rec = {"full_name": "Francesca Hong", "office_title": "Representative to the Assembly"}
    assert politician_display(rec) == "Francesca Hong · Representative to the Assembly"


def test_politician_display_all_fields():
    rec = {
        "full_name": "Thomas P. Tiffany",
        "office_title": "U.S. Representative",
        "district_label": "Congressional District 7",
        "government_name": "United States Federal Government",
    }
    assert politician_display(rec) == (
        "Thomas P. Tiffany · U.S. Representative · Congressional District 7 "
        "· United States Federal Government"
    )


def test_politician_display_omits_empty_without_stray_separators():
    rec = {"full_name": "Janet Hong", "office_title": "", "district_label": "",
           "government_name": ""}
    assert politician_display(rec) == "Janet Hong"


def test_politician_display_skips_district_that_repeats_the_office():
    # essentials stores d.label == o.title for many single-seat offices
    # ("Texas Attorney General" twice); printing it twice is noise.
    rec = {"full_name": "Ken Paxton", "office_title": "Texas Attorney General",
           "district_label": "Texas Attorney General", "government_name": ""}
    assert politician_display(rec) == "Ken Paxton · Texas Attorney General"


def test_politician_display_drops_a_district_contained_in_the_office():
    # 1141 prod rows are redundant by containment, not exact equality.
    rec = {"full_name": "Tara T. Hong",
           "office_title": "Representative, 18th Middlesex District",
           "district_label": "18th Middlesex District", "government_name": ""}
    assert politician_display(rec) == (
        "Tara T. Hong · Representative, 18th Middlesex District")


def test_politician_display_keeps_the_longer_side_when_office_is_contained():
    # When they differ, the district is often the MORE informative side.
    rec = {"full_name": "Ken Paxton", "office_title": "Attorney General",
           "district_label": "Texas Attorney General", "government_name": ""}
    assert politician_display(rec) == "Ken Paxton · Texas Attorney General"


def test_politician_display_keeps_both_when_neither_contains_the_other():
    rec = {"full_name": "Thomas P. Tiffany", "office_title": "U.S. Representative",
           "district_label": "Congressional District 7", "government_name": ""}
    assert politician_display(rec) == (
        "Thomas P. Tiffany · U.S. Representative · Congressional District 7")


def _cand(position_name="Governor", state="WI", primary_party="Republican",
          election_type="primary", year=2026, status="active"):
    return {"position_name": position_name, "state": state,
            "primary_party": primary_party, "election_type": election_type,
            "year": year, "status": status}


def test_candidacy_display_none_is_a_warning_string():
    from gui.politicians import NO_CANDIDACIES
    assert candidacy_display([]) == NO_CANDIDACIES
    assert candidacy_display(None) == NO_CANDIDACIES
    assert NO_CANDIDACIES == "no candidacies"


def test_candidacy_display_one_matches_race_display():
    expected = race_display("Governor", 2026, "WI", "Republican", "primary")
    assert candidacy_display([_cand()]) == f"running: {expected}"
    assert "WI · Governor · Republican primary · 2026" in candidacy_display([_cand()])


def test_candidacy_display_joins_several_with_semicolons():
    out = candidacy_display([_cand(), _cand(election_type="general", primary_party="")])
    assert out.startswith("running: ")
    assert out.count("; ") == 1
    assert "Republican primary" in out and "General" in out


def test_candidacy_display_prefixes_non_active_status():
    # No "running:" lead when nothing is active — "running: withdrawn: ..." reads
    # as nonsense, and a withdrawn-only person is exactly the case a curator
    # needs to notice.
    out = candidacy_display([_cand(status="withdrawn")])
    assert out == "withdrawn: WI · Governor · Republican primary · 2026"


def test_candidacy_display_treats_filed_as_running():
    # candidate_status is {active, filed, withdrawn} and "filed" is 111 live rows.
    # A filed candidate IS contesting the race, so prefixing it like a withdrawal
    # and dropping the "running:" lead would misread the data.
    assert candidacy_display([_cand(status="filed")]) == (
        "running: WI · Governor · Republican primary · 2026")


def test_candidacy_display_prefix_is_the_actual_status():
    # Pins that the prefix comes from the data, not a hardcoded "withdrawn".
    assert candidacy_display([_cand(status="disqualified")]).startswith("disqualified: ")


def test_candidacy_display_missing_status_counts_as_running():
    c = _cand()
    del c["status"]
    assert candidacy_display([c]).startswith("running: ")


def test_candidacy_display_normalizes_status_case_and_whitespace():
    assert candidacy_display([_cand(status="  ACTIVE ")]).startswith("running: ")


def test_candidacy_display_exactly_three_has_no_tail():
    cands = [_cand(position_name=f"Office {i}") for i in range(3)]
    out = candidacy_display(cands)
    assert "more" not in out
    assert out.count("; ") == 2


def test_candidacy_display_leads_with_running_when_any_is_active():
    out = candidacy_display([_cand(status="withdrawn"),
                             _cand(position_name="Senate")])
    assert out.startswith("running: ")
    assert "withdrawn: WI · Governor" in out


def test_candidacy_display_caps_at_three_and_counts_the_rest():
    cands = [_cand(position_name=f"Office {i}") for i in range(5)]
    out = candidacy_display(cands)
    # 3 shown => 2 separators between them, plus one before the "+N more" tail,
    # which is itself just another item in the list.
    assert out.count("; ") == 3
    assert out.endswith("; +2 more")
    assert "Office 3" not in out


def test_candidacy_display_skips_malformed_entries():
    out = candidacy_display(["not-a-dict", _cand(), None])
    assert out == "running: WI · Governor · Republican primary · 2026"


def test_candidacy_display_all_malformed_reads_as_none():
    assert candidacy_display(["not-a-dict", None]) == "no candidacies"


def test_mark_duplicate_names_leaves_unique_names_alone():
    rows = [{"full_name": "Mandela Barnes"}, {"full_name": "Kelda Roys"}]
    out = mark_duplicate_names(rows)
    assert [r.get("duplicate_note", "") for r in out] == ["", ""]


def test_mark_duplicate_names_flags_both_rows_of_a_pair():
    rows = [{"full_name": "Francesca Hong"}, {"full_name": "Francesca Hong"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] == "⚠ 2 results share this name" for r in out)


def test_mark_duplicate_names_ignores_middle_initials_and_case():
    rows = [{"full_name": "Thomas P. Tiffany"}, {"full_name": "thomas tiffany"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] for r in out)


def test_mark_duplicate_names_counts_the_whole_group():
    rows = [{"full_name": "Mike Rogers"} for _ in range(3)]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] == "⚠ 3 results share this name" for r in out)


def test_mark_duplicate_names_flags_genuinely_different_people_too():
    # Two real distinct Mike Rogers still both get flagged — correct: the
    # curator must look, and we can't tell them apart from names alone.
    rows = [{"full_name": "Mike Rogers", "office_title": "U.S. Representative"},
            {"full_name": "Mike Rogers", "office_title": "Senator"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] for r in out)


def test_mark_duplicate_names_does_not_collide_two_juniors():
    # Without dropping the suffix both would key to "john jr" — prod has plenty
    # of these (John G. Roberts Jr., John P. Wiley Jr.).
    rows = [{"full_name": "John G. Roberts Jr."}, {"full_name": "John P. Wiley Jr."}]
    out = mark_duplicate_names(rows)
    assert [r["duplicate_note"] for r in out] == ["", ""]


def test_mark_duplicate_names_still_matches_across_a_suffix():
    rows = [{"full_name": "Harold Ford III"}, {"full_name": "Harold Ford"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] == "⚠ 2 results share this name" for r in out)


def test_mark_duplicate_names_does_not_group_nameless_rows():
    # Two rows with no name are not "the same person" — without the empty-key
    # guard every nameless row would flag every other one.
    rows = [{"full_name": ""}, {"full_name": None}, {}]
    out = mark_duplicate_names(rows)
    assert [r["duplicate_note"] for r in out] == ["", "", ""]


def test_mark_duplicate_names_collapses_a_middle_name_on_purpose():
    # first+last for 3+ tokens means a full middle name is dropped, so these
    # collide. That is the intended bias: a needless second glance costs nothing,
    # a missed duplicate costs a silently detached meeting. Pinned so a later
    # "fix" to key on all tokens can't quietly reopen the false-negative case.
    rows = [{"full_name": "Mary Kate Olsen"}, {"full_name": "Mary Olsen"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] == "⚠ 2 results share this name" for r in out)


def test_mark_duplicate_names_does_not_flag_a_lone_row():
    out = mark_duplicate_names([{"full_name": "Thomas P. Tiffany"}])
    assert out[0]["duplicate_note"] == ""


# --- end-to-end search wiring against a fake cursor ---

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.executed = (sql, params)

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


# Row order matches the SELECT in _SEARCH_SQL:
# id, full_name, slug, office_title, district_label, government_name, candidacies
_TIFFANY_ROW = (
    "a8f96324-50ac-4fa1-b57b-47a998306fe8", "Thomas P. Tiffany", None,
    "U.S. Representative", "Congressional District 7",
    "United States Federal Government",
    [{"position_name": "Governor", "state": "WI", "primary_party": "Republican",
      "election_type": "primary", "year": 2026, "status": "active"}],
)


def _fake_db(monkeypatch, rows):
    cur = _FakeCursor(rows)
    monkeypatch.setattr(politicians, "_db_url", lambda: "postgres://fake")
    monkeypatch.setattr(politicians.psycopg2, "connect", lambda url: _FakeConn(cur))
    return cur


def test_search_maps_a_row_to_a_labelled_result(monkeypatch):
    _fake_db(monkeypatch, [_TIFFANY_ROW])
    out = politicians.search_politicians_safe("thomas tiffany")
    assert out["error"] is None
    (r,) = out["results"]
    assert r["politician_id"] == "a8f96324-50ac-4fa1-b57b-47a998306fe8"
    assert r["politician_slug"] is None
    assert r["full_name"] == "Thomas P. Tiffany"
    assert r["display"] == (
        "Thomas P. Tiffany · U.S. Representative · Congressional District 7 "
        "· United States Federal Government"
    )
    assert r["candidacy_display"] == "running: WI · Governor · Republican primary · 2026"
    assert r["candidacy_warn"] is False
    assert r["duplicate_note"] == ""


def test_search_builds_one_and_ed_clause_per_token(monkeypatch):
    cur = _fake_db(monkeypatch, [])
    politicians.search_politicians_safe("thomas tiffany")
    sql, params = cur.executed
    # 4 name fields x 2 tokens, plus the limit
    assert len(params) == 9
    assert params[-1] == 10
    assert params.count("%thomas%") == 4 and params.count("%tiffany%") == 4


def test_search_dedupes_on_politician_id_and_ranks_outside_it(monkeypatch):
    cur = _fake_db(monkeypatch, [])
    politicians.search_politicians_safe("paxton")
    sql, _ = cur.executed
    # collapses the office_current_holder fan-out
    assert "DISTINCT ON (p.id)" in sql
    # a real office beats a "Candidate for ..." placeholder inside the DISTINCT.
    # Doubled % because the SQL still has to survive psycopg2's own parameter
    # binding after str.format has run — str.format leaves %% untouched.
    assert "ILIKE 'Candidate for%%'" in sql
    # ranking + LIMIT sit OUTSIDE the dedupe, so a candidate can't be truncated
    # away by non-candidates on a common surname
    assert "(candidacies IS NULL)" in sql
    outer = sql.rsplit(") t", 1)[1]
    assert "ORDER BY" in outer and "LIMIT" in outer


def test_search_flags_duplicate_names(monkeypatch):
    hong_cand = ("dfe4ad6a", "Francesca Hong", None, "", "", "",
                 [{"position_name": "Governor", "state": "WI",
                   "primary_party": "Democratic", "election_type": "primary",
                   "year": 2026, "status": "active"}])
    hong_office = ("f1212497", "Francesca Hong", None,
                   "Representative to the Assembly", "Assembly District 76", "", None)
    _fake_db(monkeypatch, [hong_cand, hong_office])
    out = politicians.search_politicians_safe("hong")
    assert [r["duplicate_note"] for r in out["results"]] == [
        "⚠ 2 results share this name"] * 2
    # the one with no race edge says so — the signal that was missing
    assert out["results"][1]["candidacy_display"] == "no candidacies"
    assert out["results"][1]["candidacy_warn"] is True
    assert out["results"][0]["candidacy_warn"] is False


def test_search_parses_candidacies_delivered_as_json_text(monkeypatch):
    # psycopg2 hands back json as str unless a typecaster is registered
    row = list(_TIFFANY_ROW)
    row[6] = json.dumps(_TIFFANY_ROW[6])
    _fake_db(monkeypatch, [tuple(row)])
    out = politicians.search_politicians_safe("tiffany")
    assert out["results"][0]["candidacy_display"].startswith("running: WI · Governor")


def test_search_treats_null_candidacies_as_none(monkeypatch):
    row = list(_TIFFANY_ROW)
    row[6] = None
    _fake_db(monkeypatch, [tuple(row)])
    out = politicians.search_politicians_safe("tiffany")
    assert out["results"][0]["candidacies"] == []
    assert out["results"][0]["candidacy_display"] == "no candidacies"
    assert out["results"][0]["candidacy_warn"] is True


def test_search_short_query_returns_empty_without_connecting(monkeypatch):
    cur = _fake_db(monkeypatch, [_TIFFANY_ROW])
    out = politicians.search_politicians_safe("x")
    assert out == {"results": [], "error": None}
    assert cur.executed is None


def test_search_query_with_no_usable_tokens_returns_empty(monkeypatch):
    cur = _fake_db(monkeypatch, [_TIFFANY_ROW])
    out = politicians.search_politicians_safe("!!!")     # >=2 chars, no tokens
    assert out == {"results": [], "error": None}
    assert cur.executed is None


def test_search_no_db_url_returns_empty(monkeypatch):
    monkeypatch.setattr(politicians, "_db_url", lambda: None)
    assert politicians.search_politicians_safe("tiffany") == {"results": [], "error": None}


def test_search_swallows_db_errors(monkeypatch):
    monkeypatch.setattr(politicians, "_db_url", lambda: "postgres://fake")

    def boom(url):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(politicians.psycopg2, "connect", boom)
    out = politicians.search_politicians_safe("tiffany")
    assert out["results"] == []
    assert out["error"]                       # a message, not a crash


def test_search_honours_an_explicit_limit(monkeypatch):
    cur = _fake_db(monkeypatch, [])
    politicians.search_politicians_safe("tiffany", limit=25)
    _sql, params = cur.executed
    assert params[-1] == 25


# --- integration: the two things a fake cursor structurally cannot prove -------
# The SQL-string assertions above would pass unchanged if the DISTINCT ON tie-break
# sorted the wrong way, or if the outer ranking never took effect — the fake cursor
# returns rows in the order given. These run only when DATABASE_URL is set.

#
# They take conftest's `live_db` fixture, which both skips them when no database
# was provided and re-injects the URL that the autouse `_no_real_db_env` strips.
# Capturing it here instead would be wrong: by the time this module is imported,
# earlier test modules have done `import run_local`, which setdefault()s
# DATABASE_URL from .env.local — so a local gate would read a leaked value and run
# these against production on a bare `pytest tests/`. conftest is imported first,
# which is why the capture lives there.


def test_live_fanout_collapses_to_the_real_office(live_db):
    # Harriet M. Hageman holds BOTH "U.S. Representative" and the placeholder
    # "Candidate for U.S. Senate — Wyoming" in office_current_holder. One row must
    # come back, carrying the real office — if the boolean tie-break inverted, the
    # placeholder would win and nothing else would notice.
    out = politicians.search_politicians_safe("hageman")
    hers = [r for r in out["results"] if r["full_name"].startswith("Harriet")]
    assert len(hers) == 1, [r["display"] for r in hers]
    assert hers[0]["office_title"] == "U.S. Representative"
    assert "Candidate for" not in hers[0]["display"]
    assert "U.S. Senate Wyoming" in hers[0]["candidacy_display"]



def test_live_candidate_row_outranks_its_office_holding_twin(live_db):
    # Two Francesca Hong person rows exist; only one carries the WI Governor edge,
    # and it must sort FIRST so the curator's eye lands on the right one.
    out = politicians.search_politicians_safe("hong")
    hongs = [r for r in out["results"] if r["full_name"] == "Francesca Hong"]
    assert len(hongs) == 2, [r["display"] for r in hongs]
    assert hongs[0]["candidacy_warn"] is False
    assert "Governor" in hongs[0]["candidacy_display"]
    assert hongs[1]["candidacy_warn"] is True
    assert hongs[1]["candidacy_display"] == politicians.NO_CANDIDACIES
    assert all(r["duplicate_note"] for r in hongs)



def test_live_an_inactive_person_who_is_an_active_candidate_is_findable(live_db):
    # is_active = false but candidate_status = active (Murphy TX council 2026).
    # Before the IN clause this returned zero — a silent "no such person".
    out = politicians.search_politicians_safe("andrew chase")
    assert [r["full_name"] for r in out["results"]] == ["Andrew Chase"]
    assert out["results"][0]["candidacy_warn"] is False


# --- review_api delegation ---

def test_review_api_delegates_to_the_direct_db_search(monkeypatch):
    from gui import review_api
    sentinel = {"results": [{"politician_id": "x", "display": "X"}], "error": None}
    monkeypatch.setattr(politicians, "_db_url", lambda: "postgres://fake")
    monkeypatch.setattr(politicians, "search_politicians_safe",
                        lambda q, limit=10: sentinel)
    assert review_api.search_politicians_safe("tiffany") is sentinel


def test_review_api_falls_back_to_http_without_a_db_url(monkeypatch):
    from gui import review_api
    monkeypatch.setattr(politicians, "_db_url", lambda: None)
    calls = []

    def fake_http(q, limit=10):
        calls.append(q)
        return [{"id": "http-id", "slug": "http-slug", "full_name": "Tom Tiffany",
                 "office_title": "U.S. Representative", "district_label": "",
                 "government_name": "United States Federal Government",
                 "is_incumbent": True}]

    monkeypatch.setattr("src.essentials_client.search_politicians", fake_http)
    out = review_api.search_politicians_safe("tiffany")
    assert calls == ["tiffany"]
    (r,) = out["results"]
    assert r["politician_id"] == "http-id"
    assert r["display"] == (
        "Tom Tiffany · U.S. Representative · United States Federal Government")
    # no DB means no candidacy data — the renderer must omit line 2, not lie
    assert r["candidacy_display"] == ""
    assert r["candidacy_warn"] is False
    assert r["duplicate_note"] == ""


def test_review_api_fallback_swallows_http_errors(monkeypatch):
    from gui import review_api
    from src.essentials_client import EssentialsClientError
    monkeypatch.setattr(politicians, "_db_url", lambda: None)

    def boom(q, limit=10):
        raise EssentialsClientError("upstream down")

    monkeypatch.setattr("src.essentials_client.search_politicians", boom)
    out = review_api.search_politicians_safe("tiffany")
    assert out["results"] == []
    assert out["error"]
