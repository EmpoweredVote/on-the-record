#!/usr/bin/env python
"""Backfill agenda items for a meeting that was video-processed BEFORE the
agenda poller existed (the poller is forward-only; publish_scheduled_meeting
refuses published rows and keys on the scheduled slug).

Fetches the agenda from OnBoard for the given past date, parses + interprets
it exactly like scripts/poll_agendas.py, and attaches the items to the
EXISTING meeting row named by --slug (whatever slug the video pass used).
Items land status='upcoming'; run with --align to immediately run Pass B
alignment (spans + outcomes + flip to 'happened') against the local
transcript, which is what makes the item pages show the full record.

Usage:
  .venv/bin/python scripts/backfill_agenda.py \
      --date 2026-07-22 --slug 2026-07-22-bloomington-regular-session \
      [--dry-run] [--no-interpret] [--align] [--replace]

Refuses to touch a meeting that already has agenda_items rows unless
--replace is given (item ids are public permalinks; replacing mints new ones
and orphans anything keyed on them, e.g. votes.agenda_item_id).

Requires DATABASE_URL (and ANTHROPIC_API_KEY unless --no-interpret) in
.env.local.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date as date_cls, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local

load_env_local()  # before src.config so CS_DATA_DIR/API keys are visible

import psycopg2
import requests

from src import config
from src.agenda_interpret import interpret_item
from src.agenda_parse import parse_agenda
from src.bodies import BLOOMINGTON_COMMON_COUNCIL, classify_item
from src.models import AgendaItem
from src.onboard import fetch_meetings_window
from src.pdf_text import extract_text
from src.publish import _replace_agenda_items, _require_db_url, align_and_flip

INTERPRET_KINDS = ("ordinance", "resolution", "appointment")


def download_agenda(url: str, dest: Path) -> Path:
    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return dest


# Same shape as scripts/poll_agendas.build_items (kept separate: the poller is
# the launchd-run production path; this is a hand-run repair tool).
def build_items(parsed, body, source_url: str, source_text: str, client) -> list[AgendaItem]:
    items: list[AgendaItem] = []
    for p in parsed:
        cls = classify_item(p, body)
        summary_plain = None
        decision_plain = None
        if client is not None and cls.kind in INTERPRET_KINDS:
            result = interpret_item(client, p, source_text)
            if result.rejected_reason:
                print(f"  GATE {p.item_number} ({p.title_raw[:60]}): {result.rejected_reason}")
            else:
                summary_plain = result.summary_plain
                decision_plain = result.decision_plain
        items.append(AgendaItem(
            position=p.position,
            item_number=p.item_number,
            title_raw=p.title_raw,
            kind=cls.kind,
            source_url=source_url,
            legislation_ref=p.legislation_ref,
            summary_plain=summary_plain,
            decision_plain=decision_plain,
            stage=cls.stage,
            public_comment=cls.public_comment,
            public_comment_note=cls.public_comment_note,
        ))
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", required=True, help="meeting date, YYYY-MM-DD (past)")
    ap.add_argument("--slug", required=True,
                    help="slug of the EXISTING meetings.meetings row to attach items to")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch/parse/interpret and print, but no DB writes")
    ap.add_argument("--no-interpret", action="store_true", help="skip LLM interpretation")
    ap.add_argument("--align", action="store_true",
                    help="run Pass B alignment (align_and_flip) after inserting items")
    ap.add_argument("--replace", action="store_true",
                    help="allow replacing existing agenda_items (mints NEW item ids)")
    args = ap.parse_args()

    body = BLOOMINGTON_COMMON_COUNCIL
    day = date_cls.fromisoformat(args.date)
    # A single-day start==end OnBoard query returns [] (verified live) — query
    # a +/-1-day window and filter to the exact date locally.
    window_start = (day - timedelta(days=1)).isoformat()
    window_end = (day + timedelta(days=1)).isoformat()

    meetings = [
        m for m in fetch_meetings_window(
            window_start, window_end, title_prefix=body.meeting_title_prefix)
        if m.start[:10] == args.date
    ]
    if not meetings:
        print(f"FAILED: no {body.meeting_title_prefix!r} meeting on OnBoard for "
              f"{args.date}", file=sys.stderr)
        return 1
    if len(meetings) > 1:
        print(f"FAILED: {len(meetings)} meetings match {args.date}; refusing to guess",
              file=sys.stderr)
        return 1
    meeting = meetings[0]
    if not meeting.agenda_url:
        print(f"FAILED: OnBoard meeting {meeting.onboard_id} has no Agenda file",
              file=sys.stderr)
        return 1

    # Look up the target row FIRST so a bad --slug fails before any LLM spend.
    conn = psycopg2.connect(_require_db_url())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, status FROM meetings.meetings WHERE slug = %s",
                    (args.slug,),
                )
                row = cur.fetchone()
                if row is None:
                    print(f"FAILED: no meetings.meetings row with slug {args.slug!r}",
                          file=sys.stderr)
                    return 1
                meeting_uuid, status = row
                cur.execute(
                    "SELECT COUNT(*) FROM meetings.agenda_items WHERE meeting_id = %s",
                    (meeting_uuid,),
                )
                existing = cur.fetchone()[0]
        if existing and not args.replace:
            print(f"FAILED: meeting {args.slug!r} already has {existing} agenda_items "
                  "row(s); pass --replace to overwrite (mints NEW item ids and "
                  "orphans anything keyed on the old ones)", file=sys.stderr)
            return 1

        agendas_dir = config.DRIVE_ROOT / "agendas" / body.slug
        pdf_path = download_agenda(
            meeting.agenda_url, agendas_dir / args.slug / "agenda.pdf")
        text = extract_text(pdf_path)
        parsed = parse_agenda(text)
        if not parsed:
            print(f"FAILED: agenda parsed to zero items ({meeting.agenda_url})",
                  file=sys.stderr)
            return 1

        client = None
        if not args.no_interpret:
            from src.llm_providers import make_llm_client
            client = make_llm_client()

        items = build_items(parsed, body, meeting.agenda_url, text, client)

        print(f"\n=== Backfill agenda: {args.slug} ({args.date}, status={status}) ===")
        print(f"  agenda: {meeting.agenda_url}")
        for it in items:
            ref = f"  [{it.legislation_ref}]" if it.legislation_ref else ""
            interpreted = " +summary" if it.summary_plain else ""
            print(f"  [{it.item_number:>4}] pos {it.position:<3} {it.kind:<12}"
                  f"{interpreted}{ref} {it.title_raw[:70]}")

        if args.dry_run:
            print(f"\nDRY RUN: would attach {len(items)} item(s) to {meeting_uuid}")
            return 0

        with conn:
            with conn.cursor() as cur:
                count = _replace_agenda_items(cur, meeting_uuid, items)
        print(f"\nAttached {count} agenda item(s) to {args.slug} ({meeting_uuid})")
    finally:
        conn.close()

    if args.align:
        align_and_flip(args.slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
