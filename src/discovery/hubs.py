"""Comparable-source hub registry: model + per-race resolution (Slice 2B).

A "hub" is a comparable-source venue (a debate/forum series, a voter-guide
site, a questionnaire hub) worth checking for every race in its scope. This
module is pure Python -- no DB, no I/O in hubs_for_race -- so the selection
rule is trivially unit-testable; load_hubs is a thin, best-effort mapper over
an injected cursor, mirroring the cursor-bound style of src/discovery/db.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Hub:
    id: Optional[str] = None
    name: str = ""
    scope: str = ""              # 'global' | 'state' | 'local_type'
    state: Optional[str] = None  # 2-letter, UPPERCASE (may be None for global/local_type)
    kind: str = ""               # debate|forum|questionnaire|guide|pamphlet
    poll_method: str = ""        # 'feed' | 'scoped_search'
    domain: Optional[str] = None
    query_template: Optional[str] = None
    tos_bucket: Optional[str] = None
    active: bool = True
    added_via: str = "seed"
    notes: Optional[str] = None


def hubs_for_race(hubs: "list[Hub]", *, state: Optional[str],
                   scoped_only: bool = True) -> "list[Hub]":
    """Return the hubs applicable to a race in the given state.

    - every 'global' hub is included
    - every 'local_type' hub is included
    - a 'state' hub is included only when `state` is truthy AND it matches the
      hub's state case-insensitively (an unknown/empty race state can't
      confirm any state-scoped hub, so none match)
    - when scoped_only (default True), only poll_method == 'scoped_search'
      hubs are kept (feed hubs are polled via source_outlets, not this lane)

    Pure: does not mutate `hubs`; preserves input order.
    """
    race_state = (state or "").upper()
    applicable = []
    for hub in hubs:
        if hub.scope == "global" or hub.scope == "local_type":
            applicable.append(hub)
        elif hub.scope == "state":
            if race_state and (hub.state or "").upper() == race_state:
                applicable.append(hub)

    if scoped_only:
        applicable = [h for h in applicable if h.poll_method == "scoped_search"]

    return applicable


def load_hubs(cur) -> "list[Hub]":
    """Best-effort loader: map active essentials.source_hubs rows to Hub."""
    cur.execute("""
        select id::text, name, scope, state, kind, poll_method, domain, query_template,
               tos_bucket, active, added_via, notes
        from essentials.source_hubs
        where active
        order by name
    """)
    return [
        Hub(id=r[0], name=r[1], scope=r[2], state=r[3], kind=r[4], poll_method=r[5],
            domain=r[6], query_template=r[7], tos_bucket=r[8], active=r[9],
            added_via=r[10], notes=r[11])
        for r in cur.fetchall()
    ]
