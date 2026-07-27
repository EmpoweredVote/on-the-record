// Date/time presentation for scheduled meetings. starts_at is a UTC instant
// (timestamptz normalized); the meeting's own IANA zone arrives separately in
// `timezone`. A Bloomington 6:30 PM meeting must render as 6:30 PM for every
// viewer, so we format with Intl in the meeting's zone, falling back to
// date-only when either piece is missing.
import type { Meeting } from "./types";

export function formatMeetingWhen(
  date: string,
  startsAt: string | null,
  timezone: string | null
): string {
  if (startsAt && timezone) {
    try {
      return new Intl.DateTimeFormat("en-US", {
        weekday: "long",
        month: "long",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZone: timezone,
      }).format(new Date(startsAt));
    } catch {
      // Unknown zone string — fall through to date-only.
    }
  }
  // Noon avoids the UTC-midnight-rolls-back-a-day pitfall for date-only input.
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(new Date(`${date}T12:00:00`));
}

export interface DateGroup {
  date: string;
  meetings: Meeting[];
}

/** Adjacent-group meetings by meeting_date, preserving the incoming order. */
export function groupByDate(meetings: Meeting[]): DateGroup[] {
  const groups: DateGroup[] = [];
  for (const m of meetings) {
    const last = groups[groups.length - 1];
    if (last && last.date === m.meeting_date) {
      last.meetings.push(m);
    } else {
      groups.push({ date: m.meeting_date, meetings: [m] });
    }
  }
  return groups;
}
