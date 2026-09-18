"""Canned, offline stand-ins for Tavily search/fetch and OpenRouter chat calls.

Everything in this file is DETERMINISTIC and AUTHORED (not randomly generated,
not a real model or API response). It exists so `bakeoff.py --dry-run` can
exercise the real orchestration code in engine_a.py / engine_b.py / judge.py
(the bounded loop, turn/cap counting, JSON extraction, scoring math, report
rendering) with zero network calls and zero cost.

The transformation is intentionally simple and disclosed here rather than
hidden: each fixture engine "finds" a slice of the real ground-truth sources
for a race (so recall is naturally < 1, since only a slice is returned), plus
one deliberately-planted problem per engine so every judge trap category
(hallucinated / stale / advocacy) fires at least once somewhere in the run:

  - Engine A (search-agent loop) fixture: returns the top ~60% of a race's
    ground-truth sources (by tier) verbatim, PLUS one fabricated URL that does
    not exist in ground truth at all (hallucination probe).
  - Engine B (web-plugin) fixture: returns a smaller ~40% slice, PLUS the
    race's last (lowest-priority) ground-truth source re-labeled as if the
    engine believed it was still showing prior-cycle content (stale probe),
    PLUS -- when the race has a non-neutral ground-truth source -- that source
    mislabeled neutral:true (advocacy-mislabel probe).
  - Engine C (hub-directed loop) fixture: returns a larger ~85% slice, no
    extra planted problem -- engine_a's hallucination probe and engine_b's
    stale/advocacy probes already exercise every judge trap category
    somewhere in the run, so engine_c's fixture only needs to model the
    "checking named hubs finds more of what's actually there" story.

These numbers are NOT a real quality comparison between search-agent-loop and
web-plugin approaches -- they are fixed synthetic behavior whose only job is
to prove the harness's plumbing and scoring math end-to-end. A real run (with
API keys) replaces every function in this file with live calls.
"""
from __future__ import annotations

import copy

from common import load_ground_truth

_GT_BY_SLUG = {r["race"]: r for r in load_ground_truth()}


def _as_found(source: dict, **overrides) -> dict:
    """Ground-truth source -> a "found" dict (same shape, minus tier)."""
    found = {
        "type": source["type"],
        "exists": source["exists"],
        "url": source["url"],
        "medium": source["medium"],
        "neutral": source["neutral"],
        "tos": source["tos"],
        "notes": source.get("notes", ""),
    }
    found.update(overrides)
    return found


def engine_a_found(race_slug: str) -> list[dict]:
    race = _GT_BY_SLUG[race_slug]
    srcs = sorted(race["sources"], key=lambda s: s["tier"])
    keep_n = max(1, (len(srcs) * 3 + 4) // 5)  # ~60%, rounded, at least 1
    found = [_as_found(s) for s in srcs[:keep_n]]
    found.append(
        {
            "type": "forum",
            "exists": "yes",
            "url": f"https://civic-forum-archive.example.net/{race_slug}-2026-general-forum",
            "medium": "video",
            "neutral": True,
            "tos": "other",
            "notes": (
                "[dry-run fixture] A forum the search-agent loop reports finding. "
                "Not present in ground truth -- planted hallucination probe."
            ),
        }
    )
    return found


def engine_b_found(race_slug: str) -> list[dict]:
    race = _GT_BY_SLUG[race_slug]
    srcs = sorted(race["sources"], key=lambda s: s["tier"])
    keep_n = max(1, (len(srcs) * 2 + 4) // 5)  # ~40%, rounded, at least 1
    found = [_as_found(s) for s in srcs[:keep_n]]

    # Stale-cycle probe: re-report the lowest-priority GT source as current
    # when the fixture "believes" it's actually prior-cycle content.
    if len(srcs) > keep_n:
        stale_src = srcs[-1]
    else:
        stale_src = srcs[-1]
    found.append(
        _as_found(
            stale_src,
            exists="yes",
            notes=(
                "[dry-run fixture] Web-search plugin reports this as the current "
                "2026 source. Planted stale/wrong-cycle probe -- judge should flag it."
            ),
        )
    )

    # Advocacy-mislabel probe: if the race has a non-neutral GT source, report
    # it as neutral.
    non_neutral = [s for s in srcs if s.get("neutral") is False]
    if non_neutral:
        found.append(
            _as_found(
                non_neutral[0],
                neutral=True,
                notes=(
                    "[dry-run fixture] Web-search plugin labels this neutral. "
                    "Ground truth says neutral:false -- planted advocacy-mislabel probe."
                ),
            )
        )
    return found


def engine_c_found(race_slug: str) -> list[dict]:
    race = _GT_BY_SLUG[race_slug]
    srcs = sorted(race["sources"], key=lambda s: s["tier"])
    keep_n = max(1, (len(srcs) * 17 + 19) // 20)  # ~85%, rounded, at least 1
    return [_as_found(s) for s in srcs[:keep_n]]


# --- Scripted multi-turn "LLM" responses for Engine A's bounded loop --------
# A minimal 3-turn script (SEARCH -> FETCH -> FINAL) so the real loop code in
# engine_a.py actually iterates, calls the mock Tavily client, appends
# tool-result messages, and parses a final JSON payload -- rather than
# shortcutting straight to a canned return value.


def scripted_agent_turns(race_slug: str) -> list[str]:
    race = _GT_BY_SLUG[race_slug]
    query = f"{race['candidates'][0]} {race['level']} {race['state']} debate forum questionnaire 2026"
    first_url = race["sources"][0]["url"] if race["sources"] else "https://example.org/"
    final_json = _json_dumps_compact(engine_a_found(race_slug))
    return [
        f"SEARCH: {query}",
        f"FETCH: {first_url}",
        f"FINAL: {final_json}",
    ]


def mock_tavily_search_results(race_slug: str, query: str) -> list[dict]:
    race = _GT_BY_SLUG[race_slug]
    results = []
    for s in race["sources"][:3]:
        results.append(
            {
                "title": f"{s['type']} - {race['race']}",
                "url": s["url"],
                "content": s.get("notes", "")[:200],
            }
        )
    return results


def mock_fetch_page(url: str) -> str:
    for race in _GT_BY_SLUG.values():
        for s in race["sources"]:
            if s["url"] == url:
                return (
                    f"[dry-run fixture page for {url}]\n"
                    f"Type: {s['type']}. Exists: {s['exists']}. Notes: {s.get('notes','')}"
                )
    return f"[dry-run fixture page for {url}] (no canned content; generic placeholder)"


def _json_dumps_compact(obj) -> str:
    import json

    return json.dumps(obj)


# --- Canned single-shot final content for Engine B --------------------------


def mock_engine_b_response_text(race_slug: str) -> str:
    return _json_dumps_compact(engine_b_found(race_slug))
