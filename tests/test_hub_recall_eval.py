"""Offline unit tests for the pure hub-recall scorer. No network, no DB."""
from src.discovery import hub_recall_eval as hre


def test_normalize_url_strips_scheme_www_query_fragment_and_trailing_slash():
    assert hre.normalize_url("https://www.Ballotpedia.org/Karen_Bass/") == "ballotpedia.org/karen_bass"
    assert hre.normalize_url("http://laist.com/x?y=1#z") == "laist.com/x"
    assert hre.normalize_url("ballotpedia.org/Nithya_Raman") == "ballotpedia.org/nithya_raman"
    assert hre.normalize_url("") == ""


def test_registrable_domain_reduces_subdomains():
    assert hre.registrable_domain("https://news.azpm.org/p/x") == "azpm.org"
    assert hre.registrable_domain("https://onyourballot.vote411.org/x") == "vote411.org"
    assert hre.registrable_domain("ballotpedia.org") == "ballotpedia.org"
    assert hre.registrable_domain("https://princetontx.new.swagit.com/videos/1") == "swagit.com"
    assert hre.registrable_domain("https://vote.utah.gov/a") == "utah.gov"


def test_source_in_urls_matches_primary_and_accept_urls_ignoring_query():
    src = {"id": "s0", "url": "https://ballotpedia.org/Karen_Bass",
           "accept_urls": ["https://ballotpedia.org/Nithya_Raman"]}
    assert hre.source_in_urls(src, ["https://ballotpedia.org/Karen_Bass?x=1"]) is True
    assert hre.source_in_urls(src, ["http://www.ballotpedia.org/Nithya_Raman/"]) is True
    assert hre.source_in_urls(src, ["https://ballotpedia.org/Someone_Else"]) is False
    assert hre.source_in_urls(src, []) is False


def test_is_addressable_uses_registrable_domain_on_both_sides():
    src = {"id": "s0", "url": "https://news.azpm.org/p/x"}
    assert hre.is_addressable(src, ["azpm.org"]) is True          # exact registrable
    assert hre.is_addressable(src, ["news.azpm.org"]) is True     # subdomain hub -> same registrable
    assert hre.is_addressable(src, ["ballotpedia.org"]) is False
    # addressable via an accept_url on a hub domain
    src2 = {"id": "s1", "url": "https://nbclosangeles.com/x",
            "accept_urls": ["https://laist.com/y"]}
    assert hre.is_addressable(src2, ["laist.com"]) is True


def test_score_run_flags_each_source():
    gt = [
        {"id": "a", "url": "https://ballotpedia.org/A"},
        {"id": "b", "url": "https://news.azpm.org/b"},
        {"id": "c", "url": "https://vote411.org/c"},
    ]
    rows = hre.score_run(gt, hub_domains=["ballotpedia.org", "azpm.org"],
                         found_urls=["https://ballotpedia.org/A", "https://news.azpm.org/b"],
                         accepted_urls=["https://ballotpedia.org/A"])
    by_id = {r["id"]: r for r in rows}
    assert by_id["a"] == {"id": "a", "addressable": True, "retrieved": True, "accepted": True}
    assert by_id["b"] == {"id": "b", "addressable": True, "retrieved": True, "accepted": False}
    assert by_id["c"] == {"id": "c", "addressable": False, "retrieved": False, "accepted": False}


def test_recalls_from_per_source_math():
    rows = [
        {"id": "a", "addressable": True, "retrieved": True, "accepted": True},
        {"id": "b", "addressable": True, "retrieved": True, "accepted": False},
        {"id": "c", "addressable": False, "retrieved": True, "accepted": True},
        {"id": "d", "addressable": False, "retrieved": False, "accepted": False},
    ]
    r = hre.recalls_from_per_source(rows)
    assert r["n_gt"] == 4 and r["n_addressable"] == 2
    assert r["addressable_recall"] == 0.5           # 1 of 2 addressable accepted
    assert r["overall_recall"] == 0.5               # 2 of 4 accepted
    assert r["retrieval_recall_overall"] == 0.75    # 3 of 4 retrieved
    assert r["retrieval_recall_addressable"] == 1.0 # 2 of 2 addressable retrieved


def test_recalls_none_when_no_addressable():
    rows = [{"id": "a", "addressable": False, "retrieved": False, "accepted": False}]
    r = hre.recalls_from_per_source(rows)
    assert r["addressable_recall"] is None
    assert r["retrieval_recall_addressable"] is None
    assert r["overall_recall"] == 0.0


def test_precision():
    gt = [{"id": "a", "url": "https://ballotpedia.org/A"}]
    p = hre.precision(gt, ["https://ballotpedia.org/A", "https://spam.com/x"])
    assert p == {"n_accepted": 2, "n_matched": 1, "precision": 0.5}
    assert hre.precision(gt, [])["precision"] is None


def test_majority_is_strict():
    assert hre.majority(2, 3) is True
    assert hre.majority(1, 3) is False
    assert hre.majority(2, 4) is False   # tie is not a majority
    assert hre.majority(3, 4) is True


def test_majority_per_source_rolls_up_counts():
    recs = [{"id": "a", "addressable": True, "retrieved_count": 3, "accepted_count": 2}]
    out = hre.majority_per_source(recs, n_runs=3)
    assert out == [{"id": "a", "addressable": True, "retrieved": True, "accepted": True}]
