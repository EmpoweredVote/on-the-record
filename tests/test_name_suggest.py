from __future__ import annotations

import json

from src.name_candidates import Candidate
from src.name_lookup import ResearchCache, ResearchFailed, ResearcherUnavailable
from src.name_suggest import Deps, read_captions_text, suggest_for_candidate, suggest_names, write_suggestions
from src.roster import RosterMember

PAGE = "<html><body><p>Rabbi Aaron Spiegel, Executive Director, Indy Multi-Faith Alliance</p></body></html>"


def cand(name, *, tier="medium", titled=False, partial=False, affiliation=None, conflict=None, label="W"):
    return Candidate(label=label, name=name, tier=tier, role=None, titled=titled, partial=partial,
                     affiliation=affiliation, evidence=[], conflict=conflict)


class DB:
    def __init__(self, pols=None, local=None):
        self.pols, self.local = pols or {}, local or []

    def politicians_by_surname(self, s, st):
        return self.pols.get((s.lower(), st), [])

    def states_for_politicians(self, ids):
        return []

    def state_for_race(self, r):
        return None

    def local_people_by_name(self, n):
        return [r for r in self.local if r["name"].lower() == n.lower()]


def researcher(result):
    calls = []

    def r(name, title, affiliation, place):
        calls.append((name, title, affiliation, place))
        return result

    r.calls = calls
    return r


def run():
    return {"web_disabled": None}


def test_conflict_is_never_looked_up():
    rs = researcher({"name": "X Y", "affiliation": None, "url": "https://x"})
    lk = suggest_for_candidate(cand("Ann Lee", conflict="name_on_two_labels"), members=[], state=None,
                               place=None, deps=Deps(db=DB(), researcher=rs), run=run())
    assert (lk.source, lk.verified, lk.reason) == ("transcript", False, "conflict: name_on_two_labels") and rs.calls == []


def test_roster_first():
    m = [RosterMember(name="Liz Brown", aliases=["Liz Brown", "Brown"], politician_id="p-b")]
    lk = suggest_for_candidate(cand("Brown", titled=True, partial=True), members=m, state="IN", place=None,
                               deps=Deps(db=DB()), run=run())
    assert (lk.name, lk.source, lk.verified, lk.politician_id) == ("Liz Brown", "roster", True, "p-b")


def test_titled_uses_politicians_and_never_the_web():
    db = DB(pols={("garten", "IN"): [{"politician_id": "p-g", "full_name": "Chris Garten"}]})
    rs = researcher({"name": "Q", "affiliation": None, "url": "https://x"})
    lk = suggest_for_candidate(cand("Garten", titled=True, partial=True), members=[], state="IN", place=None,
                               deps=Deps(db=db, researcher=rs), run=run())
    assert (lk.name, lk.source, lk.politician_id) == ("Chris Garten", "politician", "p-g")
    lk2 = suggest_for_candidate(cand("Smith", titled=True, partial=True), members=[], state="IN", place=None,
                                deps=Deps(db=db, researcher=rs), run=run())
    assert (lk2.source, lk2.verified) == ("transcript", False) and rs.calls == []


def test_local_people_before_web():
    db = DB(local=[{"slug": "rachael-sample", "name": "Rachael Sample"}])
    lk = suggest_for_candidate(cand("Rachael Sample"), members=[], state=None, place=None,
                               deps=Deps(db=db, researcher=researcher(None)), run=run())
    assert (lk.source, lk.local_slug, lk.verified) == ("local_people", "rachael-sample", True)


def test_web_verified_and_unverified(tmp_path):
    rs = researcher({"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith Alliance", "url": "https://imfa.org"})
    deps = Deps(db=DB(), researcher=rs, fetch=lambda u: PAGE, cache=ResearchCache(tmp_path / "c.json"))
    lk = suggest_for_candidate(cand("Aaron Spiegal", affiliation="Indy multi-faith"), members=[], state="IN",
                               place="Indianapolis, IN", deps=deps, run=run())
    assert (lk.name, lk.source, lk.verified, lk.url) == ("Aaron Spiegel", "web", True, "https://imfa.org")
    assert rs.calls == [("Aaron Spiegal", None, "Indy multi-faith", "Indianapolis, IN")]
    # second time: cache hit, no new call
    suggest_for_candidate(cand("Aaron Spiegal", affiliation="Indy multi-faith"), members=[], state="IN",
                          place="Indianapolis, IN", deps=deps, run=run())
    assert len(rs.calls) == 1
    bad = Deps(db=DB(), researcher=researcher({"name": "Ann Lee", "affiliation": None, "url": "https://x"}),
               fetch=lambda u: "<p>nobody</p>")
    lk3 = suggest_for_candidate(cand("Ann Lee"), members=[], state=None, place=None, deps=bad, run=run())
    assert (lk3.name, lk3.source, lk3.verified, lk3.reason) == ("Ann Lee", "transcript", False, "name not on page")


def test_not_searched_when_partial_without_affiliation():
    rs = researcher(None)
    lk = suggest_for_candidate(cand("Michael", partial=True), members=[], state=None, place=None,
                               deps=Deps(db=DB(), researcher=rs), run=run())
    assert lk.reason == "not searched: partial name without affiliation" and rs.calls == []


def test_unavailable_disables_web_for_the_run():
    calls = []

    def down(*a):
        calls.append(a)
        raise ResearcherUnavailable("Claude CLI cannot run lookups — run `claude`, then /login")

    state = run()
    for n in ("Ann Lee", "Bob Ray"):
        lk = suggest_for_candidate(cand(n), members=[], state=None, place=None,
                                   deps=Deps(db=DB(), researcher=down), run=state)
        assert lk.reason == "web lookup unavailable"
    assert len(calls) == 1 and "login" in state["web_disabled"]


def test_suggest_names_end_to_end_and_write(tmp_path):
    meeting = {"meeting_id": "m1", "city": "Indianapolis", "event_kind": "council", "race_id": None, "segments": [
        {"segment_id": 0, "start_time": 0, "end_time": 5, "speaker_label": "CHAIR",
         "text": "Okay, next we will hear from Rabbi Aaron Spiegel."},
        {"segment_id": 1, "start_time": 5, "end_time": 30, "speaker_label": "W",
         "text": "Thank you. My name is Rabbi Aaron Spiegel, I'm with the Indy Multi-Faith Alliance. "
                 "I am here to support this bill and thank the committee for its time."},
    ]}
    rs = researcher({"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith Alliance", "url": "https://imfa.org"})
    out = suggest_names(meeting, tmp_path, members=[], deps=Deps(db=None, researcher=rs, fetch=lambda u: PAGE))
    [s] = out["suggestions"]
    assert s["label"] == "W" and s["tier"] == "strong" and s["lookup"]["verified"] is True
    assert {e["kind"] for e in s["evidence"]} == {"E1", "E2"}
    assert out["warnings"] == []
    p = write_suggestions(tmp_path, out)
    assert json.loads(p.read_text())["suggestions"][0]["lookup"]["url"] == "https://imfa.org"


def test_read_captions_text_strips_vtt(tmp_path):
    (tmp_path / "captions.vtt").write_text(
        "WEBVTT\nKind: captions\n\n00:00:01.000 --> 00:00:03.000\nhi<00:00:02.000><c> Angelica</c> Salas\n")
    t = read_captions_text(tmp_path)
    assert "Angelica Salas" in " ".join(t.split()) and "-->" not in t and "WEBVTT" not in t


def test_transient_failure_is_not_cached(tmp_path):
    calls = []

    def flaky(name, title, affiliation, place):
        calls.append(name)
        if len(calls) == 1:
            raise ResearchFailed("timeout")
        return None

    cache = ResearchCache(tmp_path / "c.json")
    deps = Deps(db=DB(), researcher=flaky, cache=cache)
    lk = suggest_for_candidate(cand("Ann Lee"), members=[], state=None, place=None, deps=deps, run=run())
    assert (lk.source, lk.verified, lk.reason) == ("transcript", False, "web lookup failed \u2014 try again later")
    assert cache._data == {}
    lk2 = suggest_for_candidate(cand("Ann Lee"), members=[], state=None, place=None, deps=deps, run=run())
    assert len(calls) == 2 and lk2.reason == "not found on the web"


def _meeting_two_labels(names):
    segs = []
    for i, (label, text) in enumerate(names):
        segs.append({"segment_id": i, "start_time": i * 10, "end_time": i * 10 + 9,
                     "speaker_label": label, "text": text})
    return {"meeting_id": "m", "city": "X", "event_kind": "council", "race_id": None, "segments": segs}


def test_same_roster_identity_on_two_labels_is_conflict(tmp_path):
    m = [RosterMember(name="Greg Taylor", aliases=["Greg Taylor", "Taylor"], politician_id="p-t")]
    meeting = _meeting_two_labels([
        ("CHAIR", "Next is Councilor Taylor."), ("A", "Thank you, I am Councilor Taylor and I support this."),
        ("CHAIR", "Now Councilor Taylor again."), ("B", "Councilor Taylor here, thanks.")])
    from src import name_suggest
    # synthetic path: force two candidates resolving to one roster identity
    cands = {l: cand("Taylor", titled=True, partial=True, label=l) for l in ("A", "B")}
    orig = name_suggest.build_candidates
    name_suggest.build_candidates = lambda *a, **k: cands
    try:
        out = suggest_names(meeting, tmp_path, members=m, deps=Deps(db=None))
    finally:
        name_suggest.build_candidates = orig
    assert len(out["suggestions"]) == 2
    for s in out["suggestions"]:
        assert s["conflict"] == "name_on_two_labels"
        lk = s["lookup"]
        assert (lk["verified"], lk["source"], lk["politician_id"], lk["url"]) == (False, "transcript", None, None)
        assert lk["reason"] == "conflict: name_on_two_labels"


class BrokenDB(DB):
    def politicians_by_surname(self, s, st):
        raise RuntimeError("db down")

    def local_people_by_name(self, n):
        raise RuntimeError("db down")

    def states_for_politicians(self, ids):
        raise RuntimeError("db down")


def test_db_failures_degrade(tmp_path):
    from src import name_suggest
    meeting = _meeting_two_labels([("A", "hello")])
    cands = {"A": cand("Ann Lee", label="A")}
    orig = name_suggest.build_candidates
    name_suggest.build_candidates = lambda *a, **k: cands
    try:
        m = [RosterMember(name="Z Y", aliases=["Z Y"], politician_id="p-z")]
        out = suggest_names(meeting, tmp_path, members=m, deps=Deps(db=BrokenDB(), researcher=researcher(None)))
    finally:
        name_suggest.build_candidates = orig
    assert out["state"] is None
    [s] = out["suggestions"]
    assert s["lookup"]["reason"] == "lookup error: RuntimeError" and s["lookup"]["verified"] is False


def test_extra_warnings_and_cache_saved_on_failure(tmp_path):
    from src import name_suggest
    meeting = _meeting_two_labels([("A", "hello")])
    cache = ResearchCache(tmp_path / "c.json")
    cache.put("k", None)
    orig = name_suggest.suggest_for_candidate

    def boom(*a, **k):
        raise KeyboardInterrupt

    cands = {"A": cand("Ann Lee", label="A")}
    ob = name_suggest.build_candidates
    name_suggest.build_candidates = lambda *a, **k: cands
    name_suggest.suggest_for_candidate = boom
    try:
        try:
            suggest_names(meeting, tmp_path, members=[], deps=Deps(db=None, cache=cache))
        except KeyboardInterrupt:
            pass
    finally:
        name_suggest.suggest_for_candidate = orig
        name_suggest.build_candidates = ob
    assert (tmp_path / "c.json").exists()
    out = suggest_names(meeting, tmp_path, members=[], deps=Deps(db=None), warnings=["w1"])
    assert out["warnings"] == ["w1"]


# ---- final-review fixes ----
def _web(found, page, c, place=None):
    deps = Deps(db=DB(), researcher=researcher(found), fetch=lambda u: page)
    return suggest_for_candidate(c, members=[], state=None, place=place, deps=deps, run=run())


def test_web_result_with_a_different_name_is_not_verified():
    page = "<p>Attorney General Todd Rokita, Office of the Indiana Attorney General</p>"
    lk = _web({"name": "Todd Rokita", "affiliation": "Office of the Attorney General", "url": "https://in.gov/ag"},
              page, cand("Aaron Tuttle", affiliation="Attorney General's office"))
    assert (lk.verified, lk.reason, lk.source) == (False, "different name returned", "transcript")
    assert lk.name == "Aaron Tuttle"


def test_web_result_affiliation_must_be_on_page():
    lk = _web({"name": "Aaron Spiegel", "affiliation": "Hoosier Families", "url": "https://x.org"},
              "<p>Aaron Spiegel, Bloomington chess club</p>", cand("Aaron Spiegal", affiliation="Hoosier Families"))
    assert (lk.verified, lk.reason) == (False, "affiliation not on page")


def test_web_spiegel_still_verified_and_researcher_affiliation_kept():
    lk = _web({"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith Alliance", "url": "https://imfa.org"},
              PAGE, cand("Aaron Spiegal", affiliation="Indy multi-faith"))
    assert (lk.verified, lk.name, lk.affiliation) == (True, "Aaron Spiegel", "Indy Multi-Faith Alliance")


def test_web_no_stated_affiliation_does_not_adopt_researcher_affiliation():
    lk = _web({"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith Alliance", "url": "https://imfa.org"},
              PAGE, cand("Aaron Spiegel"))
    assert lk.verified is True and lk.affiliation is None


def test_web_own_site_url_rejected():
    lk = _web({"name": "Aaron Spiegel", "affiliation": None, "url": "https://empowered.vote/people/x"},
              PAGE, cand("Aaron Spiegel"))
    assert (lk.verified, lk.reason) == (False, "own site excluded")


def test_partial_untitled_roster_hit_is_unverified_without_link():
    m = [RosterMember(name="Greg Taylor", aliases=["Greg Taylor", "Taylor"], politician_id="p-t")]
    lk = suggest_for_candidate(cand("Taylor", partial=True), members=m, state=None, place=None,
                               deps=Deps(db=DB()), run=run())
    assert (lk.name, lk.source, lk.verified, lk.politician_id, lk.reason) == (
        "Greg Taylor", "roster", False, None, "possible roster match")


def test_partial_titled_roster_hit_stays_verified():
    m = [RosterMember(name="Liz Brown", aliases=["Liz Brown", "Brown"], politician_id="p-b")]
    lk = suggest_for_candidate(cand("Brown", titled=True, partial=True), members=m, state=None, place=None,
                               deps=Deps(db=DB()), run=run())
    assert (lk.verified, lk.politician_id) == (True, "p-b")


def test_partial_expanded_by_web_is_capped():
    lk = _web({"name": "Nitya Kumar", "affiliation": "Hoosier Families", "url": "https://hf.org"},
              "<p>Nitya Kumar, policy director, Hoosier Families</p>",
              cand("Nitya", partial=True, affiliation="Hoosier Families"))
    assert (lk.verified, lk.name, lk.reason) == (False, "Nitya Kumar", "partial name expanded by web — confirm")
    assert lk.to_dict()["url"] is None


def _one_cand_meeting(monkeypatch, cands, **extra):
    from src import name_suggest
    monkeypatch.setattr(name_suggest, "build_candidates", lambda *a, **k: cands)
    m = _meeting_two_labels([("A", "hello")])
    m.update(extra)
    return m


def test_records_carry_partial_and_prefill_name(tmp_path, monkeypatch):
    rs = researcher({"name": "Aaron Spiegel", "affiliation": None, "url": "https://imfa.org"})
    cands = {"A": cand("Aaron Spiegal", label="A", affiliation="Indy multi-faith"),
             "B": cand("Nitya", label="B", partial=True)}
    meeting = _one_cand_meeting(monkeypatch, cands)
    out = suggest_names(meeting, tmp_path, members=[], deps=Deps(db=None, researcher=rs, fetch=lambda u: PAGE))
    by = {s["label"]: s for s in out["suggestions"]}
    assert (by["A"]["partial"], by["A"]["prefill_name"]) == (False, "Aaron Spiegel")
    assert (by["B"]["partial"], by["B"]["prefill_name"]) == (True, None)


def test_prefill_name_none_for_conflict(tmp_path, monkeypatch):
    m = [RosterMember(name="Greg Taylor", aliases=["Greg Taylor", "Taylor"], politician_id="p-t")]
    cands = {l: cand("Taylor", titled=True, partial=True, label=l) for l in ("A", "B")}
    meeting = _one_cand_meeting(monkeypatch, cands)
    out = suggest_names(meeting, tmp_path, members=m, deps=Deps(db=None))
    assert all(s["prefill_name"] is None and s["partial"] is True for s in out["suggestions"])


class INDB(DB):
    def state_for_race(self, r):
        return "IN" if r == "r1" else None


def test_place_passed_to_researcher_uses_inferred_state(tmp_path, monkeypatch):
    rs = researcher(None)
    meeting = _one_cand_meeting(monkeypatch, {"A": cand("Ann Lee", label="A")}, city="Indianapolis", race_id="r1")
    out = suggest_names(meeting, tmp_path, members=[], deps=Deps(db=INDB(), researcher=rs))
    assert out["state"] == "IN" and rs.calls[0][3] == "Indianapolis, IN"


def test_write_suggestions_is_atomic(tmp_path, monkeypatch):
    from src import name_suggest
    seen = []
    monkeypatch.setattr(name_suggest, "atomic_write_json",
                        lambda path, data, **k: (seen.append(path), path.write_text(json.dumps(data))))
    p = write_suggestions(tmp_path, {"suggestions": [], "note": "José"})
    assert seen == [p] and json.loads(p.read_text())["note"] == "José"


# ---- run_local._suggest_names ----
import pytest


def _fake_pipeline(monkeypatch):
    from src import name_suggest
    seen = {}

    def fake_suggest(meeting, meeting_dir, *, members, deps, warnings=None):
        seen.update(members=members, warnings=list(warnings or []), researcher=deps.researcher)
        return {"warnings": list(warnings or []), "suggestions": []}

    monkeypatch.setattr(name_suggest, "suggest_names", fake_suggest)
    monkeypatch.setenv("DATABASE_URL", "")
    return seen


@pytest.mark.parametrize("bad", ["../etc", "a/b", "..", "/abs"])
def test_run_local_suggest_names_rejects_unsafe_ids(bad, tmp_meetings_dir, tmp_config_dir, monkeypatch, capsys):
    import run_local
    seen = _fake_pipeline(monkeypatch)
    with pytest.raises(SystemExit) as e:
        run_local._suggest_names(bad)
    assert e.value.code != 0 and seen == {}
    assert "invalid meeting id" in capsys.readouterr().out.lower()


def test_run_local_suggest_names_corrupt_pipeline_state(tmp_meetings_dir, tmp_config_dir, monkeypatch, capsys):
    import run_local
    seen = _fake_pipeline(monkeypatch)
    d = tmp_meetings_dir / "m1"
    d.mkdir()
    (d / "transcript_named.json").write_text(json.dumps({"segments": []}))
    (d / "pipeline_state.json").write_text("{not json")
    run_local._suggest_names("m1")
    assert seen["members"] == []
    assert any("pipeline_state.json" in w for w in seen["warnings"])
    assert (d / "name_suggestions.json").exists()
