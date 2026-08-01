"""Search essentials.races (via DATABASE_URL + psycopg2, like gui.publish_api) and
compose a human label + a URL slug for a race. Best-effort: when the DB isn't
configured or a query fails, search returns no results rather than raising —
mirroring gui.review_api.search_politicians_safe.

The picker used to search `position_name` only, but that column holds no state
(a federal race is literally "U.S. Representative District 1", one row per state)
and uses "U.S. Representative" where a user types "congressional". State lives in
`elections.state` as a clean 2-letter code. So we parse the query into an optional
state filter + text tokens (with a small synonym map) and prefix every label with
the state, so the 39 identical "District 1" rows become distinguishable.

Two more columns carry the rest of a race's identity: `races.primary_party`
(set only on party primaries) and `elections.election_type`
(primary/general/special). Without them a state's two party primaries for the
same office are literally the same row to the eye — "KS · Governor of Kansas ·
2026" twice — with no way to tell the GOP one from the Democratic one, and no
way to tell either from the November general. Both are parsed out of the query
(so "ks gov gop primary" filters) and folded into the label and the slug."""
from __future__ import annotations

import os
import re
from typing import Optional

import psycopg2

# Tokens dropped from a race slug: the "U.S." pair and English connectives.
_SLUG_DROP = {"u", "s", "of", "the"}

# Full state name -> USPS code, covering every value seen in essentials.elections.
# Multi-word names are matched as adjacent token pairs (see parse_race_query).
_STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
_STATE_CODES = set(_STATE_NAME_TO_CODE.values())

# A search token -> extra phrase(s) to also match in position_name. Lets a user
# type the everyday word ("congressional") and still hit the stored wording
# ("U.S. Representative"). The token itself is always tried too.
_SYNONYMS = {
    "congressional": ["representative"],
    "congress": ["representative"],
    "cd": ["representative"],
    "representative": ["representative"],
    "rep": ["representative"],
    "senator": ["senate"],
    "governor": ["governor"],
    "gov": ["governor"],
}

# Query token -> ILIKE fragment matched against races.primary_party. Everyday
# shorthand ("gop", "dem") as well as the stored wording.
_PARTY_WORDS = {
    "republican": "republican", "republicans": "republican", "gop": "republican",
    "democratic": "democratic", "democrat": "democratic", "democrats": "democratic",
    "dem": "democratic", "dems": "democratic",
    "green": "green", "greens": "green",
}

# Query token -> elections.election_type value.
_TYPE_WORDS = {
    "primary": "primary", "primaries": "primary",
    "general": "general", "generals": "general",
    "special": "special",
}

# Office names that embed a contest word. Matched as an adjacent token pair and
# kept as text, so "az attorney general" searches position_name for the office
# instead of filtering to general elections.
_OFFICE_PHRASES = {"attorney general"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def race_qualifier(party: Optional[str] = None,
                   election_type: Optional[str] = None) -> str:
    """'Republican primary' / 'General' / '' — the contest a race actually is.

    `primary_party` is only ever populated on primaries, so a party with no
    election_type still reads as that party's primary."""
    p = (party or "").strip()
    t = (election_type or "").strip().lower()
    if p:
        return f"{p} {t or 'primary'}"
    return t.capitalize() if t else ""


def race_slug(position_name: str, state: Optional[str] = None,
              party: Optional[str] = None,
              election_type: Optional[str] = None) -> str:
    """A clean id token from a race's position_name (see module tests). When a
    state code is given it's prefixed (lowercased) so federal districts — which
    all slug to 'representative-district-1' — don't collide across states. The
    prefix is skipped when the slug already leads with that state token, to
    avoid 'ma-ma-house-...' for names that already embed the state.

    A non-general contest appends its qualifier ('ks-governor-kansas-republican-
    primary'), which is what keeps a state's two party primaries apart. Generals
    append nothing: they're the unmarked case, and existing meeting ids/URLs were
    minted without a suffix."""
    tokens = [t for t in _slug(position_name).split("-") if t and t not in _SLUG_DROP]
    code = (state or "").strip().lower()
    if code and (not tokens or tokens[0] != code):
        tokens = [code] + tokens
    if (election_type or "").strip().lower() != "general":
        qual = [t for t in _slug(race_qualifier(party, election_type)).split("-") if t]
        # ...but don't stutter when the office name already ends that way
        # ("Council Member Place 1 Special" in a special election).
        if qual and tokens[-len(qual):] != qual:
            tokens += qual
    return "-".join(tokens)


def race_display(position_name: str, year: Optional[int],
                 state: Optional[str] = None, party: Optional[str] = None,
                 election_type: Optional[str] = None) -> str:
    """'AZ · U.S. Representative District 1 · General · 2026'. The state prefix,
    contest qualifier and year suffix are each omitted when their value is
    None/empty, so an unadorned 'Governor' still renders as 'Governor'."""
    parts = []
    st = (state or "").strip()
    if st:
        parts.append(st)
    parts.append((position_name or "").strip())
    qual = race_qualifier(party, election_type)
    if qual:
        parts.append(qual)
    if year:
        parts.append(str(year))
    return " · ".join(parts)


def parse_race_query(q: str):
    """Split a raw picker query into (state_code | None, party | None,
    election_type | None, terms).

    `terms` is a list of text tokens, each expanded to a list of ILIKE
    alternates OR'd together (the token plus any synonyms). A leading/embedded
    full state name ("new mexico") or 2-letter code ("nm") is pulled out as the
    state filter; a party word ("gop") and a contest word ("primary") are pulled
    out the same way. All three are removed from the text terms — they live in
    other columns, so leaving them in would match position_name and find
    nothing."""
    tokens = re.findall(r"[a-z0-9]+", (q or "").lower())

    state: Optional[str] = None
    party: Optional[str] = None
    etype: Optional[str] = None
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        # Try a two-word state name first (e.g. "new mexico").
        if state is None and i + 1 < len(tokens):
            pair = f"{tokens[i]} {tokens[i + 1]}"
            if pair in _STATE_NAME_TO_CODE:
                state = _STATE_NAME_TO_CODE[pair]
                i += 2
                continue
        # An office phrase like "attorney general" stays text, both tokens.
        if i + 1 < len(tokens) and f"{tokens[i]} {tokens[i + 1]}" in _OFFICE_PHRASES:
            rest.extend(tokens[i:i + 2])
            i += 2
            continue
        tok = tokens[i]
        if state is None and tok in _STATE_NAME_TO_CODE:
            state = _STATE_NAME_TO_CODE[tok]
        elif state is None and len(tok) == 2 and tok.upper() in _STATE_CODES:
            state = tok.upper()
        elif party is None and tok in _PARTY_WORDS:
            party = _PARTY_WORDS[tok]
        elif etype is None and tok in _TYPE_WORDS:
            etype = _TYPE_WORDS[tok]
        else:
            rest.append(tok)
        i += 1

    terms = []
    for tok in rest:
        alts = [tok] + [s for s in _SYNONYMS.get(tok, []) if s != tok]
        terms.append(alts)
    return state, party, etype, terms


def _db_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


def search_races_safe(q: str, *, limit: int = 20) -> dict:
    """Best-effort race search by state and/or position_name. Returns
    {"results": [{"race_id","label","slug"}], "error": None|str} — never raises."""
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": [], "error": None}
    url = _db_url()
    if not url:
        return {"results": [], "error": None}

    state, party, etype, terms = parse_race_query(query)
    # Require *something* to filter on: a state, a party/contest word, or at
    # least one text term. A lone unrecognized 1-char scrap can't get here
    # (len(query) >= 2 guards it).
    where = []
    params: list = []
    if state:
        where.append("e.state = %s")
        params.append(state)
    if party:
        where.append("r.primary_party ILIKE %s")
        params.append(f"%{party}%")
    if etype:
        where.append("e.election_type = %s")
        params.append(etype)
    for alts in terms:
        ors = " OR ".join(["r.position_name ILIKE %s"] * len(alts))
        where.append(f"({ors})")
        params.extend(f"%{a}%" for a in alts)
    if not where:
        return {"results": [], "error": None}
    params.append(limit)
    where_sql = " AND ".join(where)

    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT r.id, r.position_name,
                           EXTRACT(YEAR FROM e.election_date)::int AS yr,
                           e.state, r.primary_party, e.election_type
                    FROM essentials.races r
                    LEFT JOIN essentials.elections e ON e.id = r.election_id
                    WHERE {where_sql}
                    ORDER BY e.election_date DESC NULLS LAST, e.state,
                             r.position_name, r.primary_party NULLS FIRST
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:  # DB down / auth / schema — stay best-effort
        return {"results": [], "error": f"race search failed: {exc}"}
    results = [
        {"race_id": str(rid),
         "label": race_display(name, yr, st, pp, et),
         "slug": race_slug(name, st, pp, et)}
        for (rid, name, yr, st, pp, et) in rows
    ]
    return {"results": results, "error": None}


def race_labels(race_ids) -> dict:
    """{race_id: display label} for the given ids, best-effort ({} on empty /
    no-DB / error). One query for the whole set — used to enrich the library."""
    ids = [str(r) for r in race_ids if r]
    if not ids:
        return {}
    url = _db_url()
    if not url:
        return {}
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.id, r.position_name,
                           EXTRACT(YEAR FROM e.election_date)::int AS yr,
                           e.state, r.primary_party, e.election_type
                    FROM essentials.races r
                    LEFT JOIN essentials.elections e ON e.id = r.election_id
                    WHERE r.id::text = ANY(%s)
                    """,
                    (ids,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return {}
    return {str(rid): race_display(name, yr, st, pp, et)
            for (rid, name, yr, st, pp, et) in rows}
