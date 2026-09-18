"""Hardened Tavily web-search util (Slice 2B).

Ported from the proven spike call (docs/superpowers/spikes/2026-09-17-slice2-
comparable-hubs/bakeoff/engine_a.py, `_tavily_search_real`). A missing API
key or any request failure must never crash the caller — this always
degrades to an empty list plus a one-line stderr warning.
"""
from __future__ import annotations

import os
import sys

import requests

TAVILY_URL = "https://api.tavily.com/search"


def tavily_search(query: str, *, max_results: int = 5) -> list[dict]:
    try:
        resp = requests.post(
            TAVILY_URL,
            json={
                "api_key": os.environ["TAVILY_API_KEY"],
                "query": (query or "").strip()[:380],  # Tavily rejects overlong queries (400)
                "max_results": max_results,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 -- a bad/missing key or failed search must not crash the caller
        print(f"  [web_search] tavily search failed ({exc}) — treating as empty", file=sys.stderr)
        return []
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": (r.get("content") or "")[:500],
        }
        for r in data.get("results", [])
    ]
