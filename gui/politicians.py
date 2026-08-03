"""Search essentials.politicians for the GUI speaker-link picker (via
DATABASE_URL + psycopg2, like gui.races and gui.publish_api). Best-effort: when
the DB isn't configured or a query fails, search returns no results rather than
raising — mirroring gui.races.search_races_safe.

Why this exists rather than calling ev-accounts /candidates/search-by-name:

essentials.politicians is the *person* table and candidacy is an edge in
essentials.race_candidates, so a picker row that shows only name + office cannot
tell you whether the person you're about to link is in the race you care about.
For Thomas P. Tiffany the office-holding row IS the WI Governor candidate; for
Francesca Hong the office-holding row is NOT (a hand-add minted a second person
row and the race edge landed on that one). Picking wrong is silent and costly:
publish._reconcile_event_races derives a meeting's races solely from
politician_id -> race_candidates, and Read & Rank reaches quotes only through the
same edge. So every row carries its candidacies, and duplicate names are flagged.

Two more upstream sharp edges this module files down: office_current_holder
returns one row per office, so someone holding both a real office and a
"Candidate for ..." placeholder fans out to two rows with the same politician_id
(DISTINCT ON collapses it); and matching the whole query as one substring means
"Thomas Tiffany" misses "Thomas P. Tiffany" (per-token matching fixes it).
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import psycopg2

from .races import race_display

# Name fields a query token is matched against, in the order the OR is built.
_NAME_FIELDS = ("full_name", "preferred_name", "first_name", "last_name")

# Candidacies shown in full before collapsing the tail into "+N more".
_MAX_CANDIDACIES = 3

# candidate_status values that mean the person is actually contesting the race.
# essentials uses {active, filed, withdrawn}; "filed" is 111 live rows and means
# the paperwork is in, so it earns the "running:" lead exactly like "active".
# Note publish.resolve_races_for_politicians ignores status entirely, so ALL
# three resolve the race on publish — the distinction here is informational.
_RUNNING_STATUSES = frozenset({"active", "filed"})

# The label for a person row with no race_candidates edge. Public because it is
# the warning case, and callers assert on it.
NO_CANDIDACIES = "no candidacies"

# Query tokens that carry no matchable signal. Generational suffixes live in
# their own `name_suffix` column, which _NAME_FIELDS doesn't search, so keeping
# them would AND in a clause nothing can satisfy.
_NOISE_TOKENS = frozenset({"jr", "sr", "ii", "iii", "iv"})


def _tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens worth matching on: bare initials and
    generational suffixes dropped. Shared by parse_name_query (which needs tokens
    a stored row could plausibly contain) and _dupe_key (which needs tokens that
    carry identity) — the two want the same normalization for the same reason."""
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) > 1 and t not in _NOISE_TOKENS]


def parse_name_query(q: str) -> list[str]:
    """Matchable lowercase tokens from a raw picker query.

    Each token becomes its own AND-ed clause, which is what lets "Thomas
    Tiffany" match the stored "Thomas P. Tiffany" — but it cuts both ways, so
    anything the stored row can't possibly carry has to be dropped or the whole
    query returns nothing. Two such cases, both verified against prod:

      "John F Kennedy"  -> stored "John Kennedy" has no F   -> 0 rows
      "Wesley Hunt Jr"  -> name_suffix isn't in _NAME_FIELDS -> 0 rows

    So single-character tokens (bare middle initials) and generational suffixes
    are dropped. Word order never mattered either way.
    """
    return _tokens(q)


def politician_display(rec: dict) -> str:
    """'Thomas P. Tiffany · U.S. Representative · Congressional District 7 ·
    United States Federal Government' — identity, empty parts omitted.

    office_title and district_label are frequently redundant: 112 prod rows store
    d.label == o.title outright ("Texas Attorney General" twice) and another 1141
    have one contained in the other ("Representative, 18th Middlesex District" /
    "18th Middlesex District"). Printing both crowds out the part that actually
    disambiguates, so when one contains the other only the longer survives —
    which side that is varies ("Attorney General" vs "Texas Attorney General"
    keeps the district).
    """
    office = (rec.get("office_title") or "").strip()
    district = (rec.get("district_label") or "").strip()
    if office and district:
        lower_office, lower_district = office.lower(), district.lower()
        if lower_district in lower_office:
            district = ""
        elif lower_office in lower_district:
            office, district = district, ""
    parts = [
        (rec.get("full_name") or "").strip(),
        office,
        district,
        (rec.get("government_name") or "").strip(),
    ]
    return " · ".join(p for p in parts if p)


def _status(c: dict) -> str:
    """Normalized candidacy status. Missing status means active, matching
    publish.resolve_races_for_politicians, which doesn't filter status."""
    return (c.get("status") or "active").strip().lower()


def _one_candidacy(c) -> Optional[tuple[str, bool]]:
    """('WI · Governor · Republican primary · 2026', True) — the label plus
    whether it's a live run. A non-running status is prefixed
    ('withdrawn: <that>', False). None when the entry isn't a usable dict.

    Returning both together keeps the dict validated and the status normalized
    exactly once per entry.
    """
    if not isinstance(c, dict):
        return None
    label = race_display(
        c.get("position_name") or "",
        c.get("year"),
        c.get("state"),
        c.get("primary_party"),
        c.get("election_type"),
    )
    if not label:
        return None
    status = _status(c)
    running = status in _RUNNING_STATUSES
    return (label if running else f"{status}: {label}", running)


def candidacy_display(candidacies) -> str:
    """'running: <race>; <race>' — or the literal 'no candidacies', which is the
    signal that this person row has no race_candidates edge and so cannot carry a
    meeting or a quote into a race.

    The 'running:' lead is dropped when nothing is running, because
    'running: withdrawn: ...' reads as nonsense and a withdrawn-only person is
    precisely the case a curator needs to notice.

    Capped at _MAX_CANDIDACIES with a '+N more' tail so one row can't run away.
    Malformed entries are skipped rather than breaking the whole label.
    """
    pairs = [p for p in (_one_candidacy(c) for c in (candidacies or [])) if p]
    if not pairs:
        return NO_CANDIDACIES
    shown = pairs[:_MAX_CANDIDACIES]
    text = "; ".join(label for label, _running in shown)
    extra = len(pairs) - len(shown)
    if extra:
        text += f"; +{extra} more"
    return f"running: {text}" if any(running for _label, running in pairs) else text


def _dupe_key(full_name: str) -> str:
    """'thomas tiffany' from 'Thomas P. Tiffany' — a loose identity key for
    spotting two rows that a curator would read as the same person.

    Bare middle initials and generational suffixes are dropped for the same
    reason parse_name_query drops them: they're display choices, not identity.
    Dropping the suffix is what stops the key collapsing to the suffix itself —
    'John G. Roberts Jr.' and 'John P. Wiley Jr.' would BOTH key to 'john jr'
    and get flagged as the same person, and prod is full of such names.
    """
    tokens = _tokens(full_name)
    if len(tokens) <= 2:
        return " ".join(tokens)
    return f"{tokens[0]} {tokens[-1]}"


def mark_duplicate_names(results: list[dict]) -> list[dict]:
    """Set `duplicate_note` on every row whose normalized name is shared with
    another row in the same response ('' on the rest). Mutates and returns the
    list.

    Deliberately flags the WHOLE group rather than guessing which row is right:
    two rows can be one person split by a bad hand-add, or two genuinely
    different people with the same name, and nothing in the name distinguishes
    those cases. The candidacy line is what tells the curator which to pick; this
    marker only says "look before you click".

    Known blind spot: nickname variants aren't caught, because "dan brotman" and
    "daniel brotman" are different keys. Prod has 21 such pairs and 9 of them have
    the dangerous shape — one row with a race edge, its twin with none (Dan/Daniel
    Brotman, Mike/Michael Thompson, Rick/Richard Bennett, ...). Closing it needs a
    nickname map; keying on the first initial instead would group "Angela Davis"
    with "Andrew Davis" and drown the signal on common surnames. The candidacy
    line still distinguishes those pairs, which is the safeguard that matters.

    The count is per-response, not global — the search is limited, so a name with
    more rows than fit can show a smaller number. Hence "results share this name"
    rather than "records exist".
    """
    keys = [_dupe_key(r.get("full_name") or "") for r in results]
    counts: dict[str, int] = {}
    for key in keys:
        if key:                      # two nameless rows aren't the same person
            counts[key] = counts.get(key, 0) + 1
    for r, key in zip(results, keys):
        n = counts.get(key, 0)
        r["duplicate_note"] = f"⚠ {n} results share this name" if n > 1 else ""
    return results


# DISTINCT ON requires its ORDER BY to lead with p.id, which is not the order we
# want to present. Limiting at that level would truncate an arbitrary N rows
# BEFORE ranking, so on a common surname the candidate could be cut entirely.
# Hence the wrap: dedupe inside, rank and LIMIT outside.
#
# is_active alone was too strict: three people are is_active = false while holding
# an ACTIVE candidacy (Andrew Chase / Laura Deel, Murphy TX council 2026; Gopal
# Ponangi, Frisco 2025), and the picker returned zero for them — indistinguishable
# from "no such person", the very failure this module exists to remove. The IN is
# deliberately UNCORRELATED so Postgres hashes it once: the correlated EXISTS form
# measured 1700ms against 160ms, while this measures 162ms. Rows that are inactive
# AND only ever withdrawn stay hidden.
_SEARCH_SQL = """
SELECT * FROM (
  SELECT DISTINCT ON (p.id)
         p.id, p.full_name, p.slug,
         COALESCE(o.title, '') AS office_title,
         COALESCE(d.label, '') AS district_label,
         COALESCE(g.name, '')  AS government_name,
         cand.candidacies
  FROM essentials.politicians p
  LEFT JOIN essentials.office_current_holder och ON och.politician_id = p.id
  LEFT JOIN essentials.offices     o  ON o.id  = och.office_id
  LEFT JOIN essentials.districts   d  ON d.id  = o.district_id
  LEFT JOIN essentials.chambers    ch ON ch.id = o.chamber_id
  LEFT JOIN essentials.governments g  ON g.id  = ch.government_id
  LEFT JOIN LATERAL (
    SELECT json_agg(json_build_object(
             'position_name',  r.position_name,
             'state',          e.state,
             'primary_party',  r.primary_party,
             'election_type',  e.election_type,
             'year',           EXTRACT(YEAR FROM e.election_date)::int,
             'status',         COALESCE(rc.candidate_status, 'active')
           ) ORDER BY e.election_date) AS candidacies
    FROM essentials.race_candidates rc
    JOIN essentials.races     r ON r.id = rc.race_id
    JOIN essentials.elections e ON e.id = r.election_id
    WHERE rc.politician_id = p.id
  ) cand ON true
  WHERE (p.is_active = true
         OR p.id IN (SELECT rc2.politician_id
                     FROM essentials.race_candidates rc2
                     WHERE COALESCE(rc2.candidate_status, 'active') <> 'withdrawn'))
    AND {token_clauses}
  ORDER BY p.id,
           (COALESCE(o.title, '') ILIKE 'Candidate for%%'),
           o.title NULLS LAST
) t
ORDER BY (candidacies IS NULL),
         (office_title = ''),
         full_name
LIMIT %s
"""


def _db_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


def _token_clauses(tokens: list[str]) -> tuple[str, list]:
    """(sql, params) for the name filter: one AND-ed clause per token, each an OR
    over _NAME_FIELDS. Only field names — from a module constant — are
    interpolated; every user value is a bound parameter.

    Known cost: essentials has a trigram index on f_unaccent(lower(full_name)),
    which this predicate cannot use (no lower(), and the OR across three
    unindexed columns would defeat it anyway), so each search seq-scans the
    politicians table — 160ms today and O(table size). Recorded as a spec
    follow-up: fixing it needs matching expression indexes in ev-accounts'
    schema, and the secondary fields can't simply be dropped (23 active rows
    match only on preferred_name, which is the nickname recall).
    """
    clauses = []
    params: list = []
    for tok in tokens:
        ors = " OR ".join(
            f"public.f_unaccent(p.{f}) ILIKE public.f_unaccent(%s)"
            for f in _NAME_FIELDS
        )
        clauses.append(f"({ors})")
        params.extend([f"%{tok}%"] * len(_NAME_FIELDS))
    return " AND ".join(clauses), params


def _coerce_candidacies(raw) -> list:
    """psycopg2 gives json back as a list when a typecaster is registered and as
    text otherwise; NULL means no race_candidates rows at all."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return list(raw) if isinstance(raw, list) else []


def search_politicians_safe(q: str, *, limit: int = 10) -> dict:
    """Best-effort politician search for the link picker. Returns
    {"results": [...], "error": None|str} — never raises.

    Every result carries `display` (identity), `candidacy_display` (which races
    this person is actually a candidate in) and `duplicate_note`, so the curator
    can see which of two same-named rows holds the race edge.
    """
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": [], "error": None}
    tokens = parse_name_query(query)
    if not tokens:
        return {"results": [], "error": None}
    url = _db_url()
    if not url:
        return {"results": [], "error": None}

    where_sql, params = _token_clauses(tokens)
    params.append(limit)
    sql = _SEARCH_SQL.format(token_clauses=where_sql)

    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:  # DB down / auth / schema — stay best-effort
        return {"results": [], "error": f"politician search failed: {exc}"}

    results = []
    for pid, full_name, slug, office, district, government, cands in rows:
        rec = {
            "politician_id": str(pid) if pid else None,
            "politician_slug": slug,
            "full_name": full_name or "",
            "office_title": office or "",
            "district_label": district or "",
            "government_name": government or "",
            "candidacies": _coerce_candidacies(cands),
        }
        rec["display"] = politician_display(rec)
        rec["candidacy_display"] = candidacy_display(rec["candidacies"])
        # Explicit flag rather than letting the client match on the label text:
        # Task 6 styles this row as a warning, and a reworded label must not be
        # able to silently turn that styling off. Derived FROM the label rather
        # than from `candidacies`, so the two can never disagree — a row whose
        # candidacies all fail to render would otherwise say "no candidacies"
        # while telling the client not to warn.
        rec["candidacy_warn"] = rec["candidacy_display"] == NO_CANDIDACIES
        results.append(rec)
    return {"results": mark_duplicate_names(results), "error": None}
