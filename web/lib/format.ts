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

// Human-friendly meeting length, e.g. "2h 14m" or "48m". Empty string when unknown.
export function formatDuration(seconds: number | null): string {
  if (!seconds || seconds < 0) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

const INTERVIEW_KINDS: EventKind[] = ["news_clip", "press_conference"];

const EVENT_KIND_LABELS: Record<EventKind, string> = {
  council: "Council",
  school_board: "School board",
  debate: "Debate",
  forum: "Forum",
  community_meeting: "Community meeting",
  floor: "Floor",
  news_clip: "News clip",
  press_conference: "Press conference",
  other: "Other",
};

export function meetingTitle(
  meeting: Pick<Meeting, "title" | "city" | "meeting_type" | "event_kind" | "event_orgs" | "source_title">
): string {
  const explicit = meeting.title?.trim();
  if (explicit) return explicit;
  if (meeting.source_title?.trim()) return meeting.source_title.trim();
  if (INTERVIEW_KINDS.includes(meeting.event_kind)) {
    const orgs = meeting.event_orgs.join(", ");
    const kindLabel = EVENT_KIND_LABELS[meeting.event_kind];
    return orgs ? `${orgs} · ${kindLabel}` : kindLabel;
  }
  return [meeting.city, meeting.meeting_type]
    .filter((part): part is string => Boolean(part?.trim()))
    .join(" ");
}

export function eventKindLabel(kind: EventKind): string {
  return EVENT_KIND_LABELS[kind] ?? kind;
}
