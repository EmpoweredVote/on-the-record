#!/usr/bin/env python
"""Poll OnBoard for upcoming Bloomington Common Council agendas and publish
them as scheduled meetings with agenda items (Pass A).

Usage:
  .venv/bin/python scripts/poll_agendas.py                 # poll + publish
  .venv/bin/python scripts/poll_agendas.py --days 14
  .venv/bin/python scripts/poll_agendas.py --dry-run       # no DB writes, no state recording
  .venv/bin/python scripts/poll_agendas.py --no-interpret  # skip LLM summaries
  .venv/bin/python scripts/poll_agendas.py --reconcile-memos  # also reconcile clerk memoranda

Agendas post the Friday before the meeting (sometimes only ~48h ahead) and
addenda can land through the meeting day, so run this daily from ~6 days out
(default window: today .. today+8). Change detection is per-meeting via the
OnBoard file created/updated marker; an unchanged agenda is skipped.

With --reconcile-memos, a lookback pass (default --lookback-days 10) also
checks recent PAST meetings for a posted clerk Memorandum and runs
publish.reconcile_memo on new/changed ones. Change detection uses a separate
memo_state.json marker file; a failed reconcile (e.g. meeting not yet
published under its scheduled slug) is NOT recorded, so it retries daily
until it succeeds or ages out of the window.

Requires DATABASE_URL (and ANTHROPIC_API_KEY unless --no-interpret) in
.env.local. Failures are loud, per-meeting, and non-fatal: each failed
meeting prints FAILED to stderr and the script exits 1 if any failed.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local

load_env_local()  # before src.config so CS_DATA_DIR/API keys are visible

from src import config
from src.agenda_interpret import interpret_item
from src.agenda_parse import parse_agenda
from src.agenda_pipeline import PollState, download_file, plan_work
from src.bodies import BLOOMINGTON_COMMON_COUNCIL, classify_item
from src.models import AgendaItem
from src.onboard import fetch_meetings_window
from src.pdf_text import extract_text
from src.publish import publish_scheduled_meeting

INTERPRET_KINDS = ("ordinance", "resolution", "appointment")


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


def reconcile_memos(body, agendas_dir: Path, *, lookback_days: int, dry_run: bool) -> int:
    """Reconcile clerk memoranda for recent past meetings. Change-detects on
    the memo file marker (separate memo_state.json); a meeting whose slug
    isn't in the DB fails loudly WITHOUT recording the marker, so it retries
    daily until the meeting publishes or ages out of the window. Returns the
    failure count."""
    from src.publish import reconcile_memo, scheduled_slug

    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    end = date.today().isoformat()
    print(f"Checking {body.slug} memoranda {start} .. {end}")
    meetings = fetch_meetings_window(start, end, title_prefix=body.meeting_title_prefix)
    state = PollState(agendas_dir / "memo_state.json")
    failures = 0
    for m in meetings:
        marker = m.memo_updated_marker
        if not marker:
            continue  # memo not posted yet
        slug = scheduled_slug(body, m.start[:10])
        if state.marker_for(slug) == marker:
            print(f"  MEMO SKIP {slug}: unchanged")
            continue
        if dry_run:
            print(f"  MEMO DRY-RUN {slug}: memo present, would reconcile")
            continue
        try:
            result = reconcile_memo(slug)
        except Exception as exc:
            failures += 1
            print(f"MEMO FAILED {slug}: {exc}", file=sys.stderr)
            continue
        if result.get("memo") is not None:
            state.record(slug, marker)
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=8, help="window size in days (default 8)")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch/parse/interpret but no DB writes and no state recording")
    ap.add_argument("--no-interpret", action="store_true", help="skip LLM interpretation")
    ap.add_argument("--reconcile-memos", action="store_true",
                    help="also reconcile clerk memoranda for recent past meetings")
    ap.add_argument("--lookback-days", type=int, default=10,
                    help="memo lookback window in days (default 10)")
    args = ap.parse_args()

    body = BLOOMINGTON_COMMON_COUNCIL
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=args.days)).isoformat()
    print(f"Polling {body.slug} agendas {start} .. {end}")

    meetings = fetch_meetings_window(start, end, title_prefix=body.meeting_title_prefix)
    print(f"  {len(meetings)} upcoming meeting(s) match {body.meeting_title_prefix!r}")

    agendas_dir = config.DRIVE_ROOT / "agendas" / body.slug
    state = PollState(agendas_dir / "poll_state.json")
    work, skipped = plan_work(meetings, state, body_slug=body.slug)
    for slug, reason in skipped:
        print(f"  SKIP {slug}: {reason}")

    client = None
    if not args.no_interpret and work:
        import anthropic
        client = anthropic.Anthropic()

    failures = 0
    for w in work:
        try:
            meeting = w.meeting
            pdf_path = download_file(meeting.agenda_url, agendas_dir / w.slug / "agenda.pdf")
            text = extract_text(pdf_path)
            parsed = parse_agenda(text)
            if not parsed:
                raise RuntimeError(f"agenda parsed to zero items ({meeting.agenda_url})")
            items = build_items(parsed, body, meeting.agenda_url, text, client)

            if args.dry_run:
                interpreted = sum(1 for i in items if i.summary_plain or i.decision_plain)
                print(f"  DRY-RUN {w.slug}: {len(items)} item(s), "
                      f"{interpreted} interpreted — no publish, no state recorded")
                for i in items:
                    ref = f" [{i.legislation_ref}]" if i.legislation_ref else ""
                    print(f"    {i.position:>2}. {i.item_number:<4} {i.kind:<15}{ref} {i.title_raw[:70]}")
                continue

            published = publish_scheduled_meeting(
                body, w.date,
                title=f"{meeting.title} — {w.date}",
                starts_at=meeting.start,
                source_url=meeting.agenda_url,
                items=items,
            )
            if published is None:
                # Video pass owns the items now; record the marker so future
                # polls don't re-download/re-interpret this agenda.
                state.record(w.slug, meeting.agenda_updated_marker)
                print(f"  SKIP {w.slug}: already published (video pass owns it; marker recorded)")
            else:
                print(f"  PUBLISHED {published}: {len(items)} agenda item(s)")
                state.record(w.slug, meeting.agenda_updated_marker)
        except Exception as exc:
            failures += 1
            print(f"FAILED {w.slug}: {exc}", file=sys.stderr)

    if args.reconcile_memos:
        failures += reconcile_memos(
            body, agendas_dir,
            lookback_days=args.lookback_days, dry_run=args.dry_run,
        )

    if failures:
        print(f"{failures} meeting(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
