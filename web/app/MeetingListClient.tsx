'use client';

import { useState, useMemo } from 'react';
import type { Meeting, EventKind } from '@/lib/types';
import { eventKindLabel } from '@/lib/format';
import MeetingCard from '@/components/MeetingCard';

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
          <MeetingCard key={m.meeting_id} meeting={m} />
        ))}
      </ul>
    </>
  );
}
