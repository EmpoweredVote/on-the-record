import type { Meeting, Segment } from "./types";

const BASE = (process.env.EV_ACCOUNTS_URL ?? "").replace(/\/$/, "");

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapMeeting(m: any): Meeting {
  return {
    meeting_id: m.id,
    slug: m.slug ?? null,
    city: m.city,
    body_slug: m.bodySlug ?? null,
    meeting_type: m.meetingType,
    meeting_date: m.date,
    source_url: m.sourceUrl ?? null,
    playback_kind: m.playbackKind ?? null,
    playback_url: m.videoUrl ?? null,
    duration_seconds: m.durationSeconds ?? null,
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapSegment(s: any): Segment {
  return {
    meeting_id: s.meetingId,
    segment_id: s.segmentIndex,
    start_time: s.startTime,
    end_time: s.endTime,
    speaker_label: s.speakerLabel ?? "",
    speaker_name: s.speakerName ?? null,
    politician_slug: s.politicianSlug ?? null,
    text: s.text,
  };
}

export async function fetchMeetings(): Promise<Meeting[]> {
  const res = await fetch(`${BASE}/api/meetings`);
  if (!res.ok) throw new Error(`meetings fetch failed: ${res.status}`);
  const data = await res.json();
  return (data as unknown[]).map(mapMeeting);
}

export async function fetchMeeting(meetingId: string): Promise<Meeting | null> {
  const res = await fetch(`${BASE}/api/meetings/${meetingId}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`meeting fetch failed: ${res.status}`);
  return mapMeeting(await res.json());
}

// ev-accounts paginates the transcript at 200 segments/page
export async function fetchSegments(meetingId: string): Promise<Segment[]> {
  const all: Segment[] = [];
  for (let page = 1; ; page++) {
    const res = await fetch(
      `${BASE}/api/meetings/${meetingId}/transcript?page=${page}`
    );
    if (!res.ok) throw new Error(`transcript fetch failed: ${res.status}`);
    const { segments, totalCount } = (await res.json()) as {
      segments: unknown[];
      page: number;
      totalCount: number;
    };
    all.push(...segments.map(mapSegment));
    if (all.length >= totalCount) break;
  }
  return all;
}
