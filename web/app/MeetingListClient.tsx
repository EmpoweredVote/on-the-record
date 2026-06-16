'use client';

import { useState, useMemo } from 'react';
import Link from 'next/link';
import type { Meeting, EventKind } from '@/lib/types';
import { eventKindLabel, formatMeetingDate, meetingTitle } from '@/lib/format';

function formatDuration(seconds: number | null): string {
  if (!seconds) return '';
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export default function MeetingListClient({ meetings }: { meetings: Meeting[] }) {
  // Derive tabs from actual event_kinds present — no hardcoding per CONTEXT specifics
  const kinds = useMemo(() => {
    const seen = new Set<EventKind>();
    meetings.forEach(m => seen.add(m.event_kind));
    return [...seen].sort();
  }, [meetings]);

  const [active, setActive] = useState<EventKind | 'all'>('all');

  const shown = active === 'all'
    ? meetings
    : meetings.filter(m => m.event_kind === active);

  return (
    <>
      {kinds.length > 1 && (
        <nav className="kindTabs" aria-label="Filter by event type">
          <button
            className={active === 'all' ? 'active' : ''}
            onClick={() => setActive('all')}
          >
            All
          </button>
          {kinds.map(k => (
            <button
              key={k}
              className={active === k ? 'active' : ''}
              onClick={() => setActive(k)}
            >
              {eventKindLabel(k)}
            </button>
          ))}
        </nav>
      )}
      <ul className="meetingList">
        {shown.map((m) => (
          <li key={m.meeting_id}>
            <Link href={`/meetings/${m.meeting_id}`}>
              <span className="meetingTitle">{meetingTitle(m)}</span>
              <span className="eventKind">{eventKindLabel(m.event_kind)}</span>
              <span className="meetingDate">{formatMeetingDate(m.meeting_date)}</span>
              {m.duration_seconds ? (
                <span className="meetingDuration">
                  {formatDuration(m.duration_seconds)}
                </span>
              ) : null}
              {m.playback_kind ? (
                <span className="hasVideo">▶ video</span>
              ) : null}
              {m.summary_preview && <span className="meetingPreview">{m.summary_preview}</span>}
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
