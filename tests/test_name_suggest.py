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
