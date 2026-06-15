import type { EventKind, Meeting } from "./types";

// Render a meeting date as a readable, locale-friendly string (e.g. "Feb 25, 2026").
// Accepts a YYYY-MM-DD string (what the API returns) or a full ISO datetime
// (legacy/cached rows). We parse only the date portion and construct the Date
// from explicit local components so the displayed day never drifts by a
// timezone offset (e.g. a UTC-midnight ISO string rendering as the prior day).
export function formatMeetingDate(value: string | null | undefined): string {
  if (!value) return "";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return value;
  const [, y, m, d] = match;
  const date = new Date(Number(y), Number(m) - 1, Number(d));
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

export function meetingTitle(
  meeting: Pick<Meeting, "title" | "city" | "meeting_type">
): string {
  const explicit = meeting.title?.trim();
  if (explicit) return explicit;
  return [meeting.city, meeting.meeting_type]
    .filter((part): part is string => Boolean(part?.trim()))
    .join(" ");
}

const EVENT_KIND_LABELS: Record<EventKind, string> = {
  council: "Council",
  school_board: "School board",
  debate: "Debate",
  forum: "Forum",
  community_meeting: "Community meeting",
  news_clip: "News clip",
  other: "Other",
};

export function eventKindLabel(kind: EventKind): string {
  return EVENT_KIND_LABELS[kind];
}
