"""CLI: cache a speaker roster for one Indiana General Assembly committee recording.

Usage:
    python iga_roster.py "https://iga.in.gov/session/2026/video/committee_judiciary_4200/?video=Judiciary_1_14_2026_1"
    python iga_roster.py <url> --slug in-senate-judiciary-2026-01-14

Writes ~/CouncilScribe/config/rosters/{slug}.json (members + agenda bill
authors/sponsors, matched to essentials.politicians), then pass
``--body {slug}`` to run_local.py. Reads DATABASE_URL (read-only).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Load .env.local (DATABASE_URL, CS_DATA_DIR) before src.config computes paths —
# same loader as run_local.py.
_env_file = Path(__file__).resolve().parent / ".env.local"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            os.environ.setdefault(_key.strip(), _val.strip())

from refresh_roster import _build_cache_payload, _cache_path_for, _write_json_atomic  # noqa: E402


def _default_slug(title: str, date: str | None) -> str:
    # "Indiana Senate Judiciary Committee — Jan. 14, 2026" -> in-senate-judiciary-2026-01-14
    body = title.split("—")[0].replace("Indiana", "in").replace("Committee", "")
    slug = re.sub(r"[^a-z0-9]+", "-", body.lower()).strip("-")
    return f"{slug}-{date}" if date else slug


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iga_roster.py", description=__doc__.splitlines()[0])
    parser.add_argument("url", help="IGA committee recording URL (page with ?video=, or the .m3u8)")
    parser.add_argument("--slug", default=None, help="Roster slug (default: derived from committee + date)")
    args = parser.parse_args(argv)

    from src import iga
    from src.iga_roster import build_roster, db_lookup
    from src.resolve import _default_fetch

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        print("ERROR: DATABASE_URL is not set (add it to .env.local).", file=sys.stderr)
        return 1

    resolved = iga.resolve_iga_video(args.url, fetch=_default_fetch)
    if resolved is None:
        print(f"ERROR: not an IGA video URL: {args.url}", file=sys.stderr)
        return 1
    response, matches = build_roster(args.url, fetch=_default_fetch, lookup=db_lookup(db_url))

    slug = args.slug or _default_slug(resolved.title or "", resolved.date)
    payload = _build_cache_payload(slug, response)
    path = _cache_path_for(slug)
    _write_json_atomic(path, payload)

    for m in matches:
        leg = m.legislator
        who = f"{leg.first_name} {leg.last_name} ({leg.chamber} D{leg.district or '?'}; {', '.join(leg.roles)})"
        print(f"  {'✓' if m.politician_id else '✗ UNMATCHED'} {who}" + (f" -> {m.politician_id}" if m.politician_id else ""))
    missing = sum(1 for m in matches if not m.politician_id)
    print(f"Wrote {path} ({len(payload['politicians'])} members{f', {missing} unmatched' if missing else ''})")
    print(f"Use: --body {slug}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
