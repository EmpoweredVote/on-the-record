import type { Appearance, Meeting, Person, PersonDetail, Segment } from "./types";

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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapPerson(p: any): Person {
  return {
    slug: p.slug,
    politician_id: p.politicianId ?? null,
    name: p.name,
    headshot_url: p.headshotUrl ?? null,
    party: p.party ?? null,
    office_title: p.officeTitle ?? null,
    district: p.district ?? null,
    jurisdiction: p.jurisdiction ?? null,
    meeting_count: p.meetingCount ?? 0,
    cities: p.cities ?? [],
    last_spoke_date: p.lastSpokeDate ?? null,
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapAppearance(a: any): Appearance {
  return {
    meeting_id: a.meetingId,
    city: a.city,
    meeting_type: a.meetingType,
    meeting_date: a.date,
    playback_kind: a.playbackKind ?? null,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    segments: ((a.segments ?? []) as any[]).map((s) => ({
      segment_id: s.segmentIndex,
      start_time: s.startTime,
      end_time: s.endTime,
      text: s.text,
    })),
  };
}

const NO_CACHE: RequestInit = { cache: "no-store" };

export async function fetchPeople(): Promise<Person[]> {
  const res = await fetch(`${BASE}/api/people`, NO_CACHE);
  if (!res.ok) throw new Error(`people fetch failed: ${res.status}`);
  const data = await res.json();
  return (data as unknown[]).map(mapPerson);
}

export async function fetchPerson(slug: string): Promise<PersonDetail | null> {
  const res = await fetch(`${BASE}/api/people/${encodeURIComponent(slug)}`, NO_CACHE);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`person fetch failed: ${res.status}`);
  const p = await res.json();
  return { ...mapPerson(p), bio_text: p.bioText ?? null };
}

export async function fetchAppearances(slug: string): Promise<Appearance[]> {
  const res = await fetch(
    `${BASE}/api/people/${encodeURIComponent(slug)}/appearances`,
    NO_CACHE
  );
  if (!res.ok) throw new Error(`appearances fetch failed: ${res.status}`);
  const { appearances } = (await res.json()) as { appearances: unknown[] };
  return appearances.map(mapAppearance);
}

export async function fetchMeetings(): Promise<Meeting[]> {
  const res = await fetch(`${BASE}/api/meetings`, NO_CACHE);
  if (!res.ok) throw new Error(`meetings fetch failed: ${res.status}`);
  const data = await res.json();
  return (data as unknown[]).map(mapMeeting);
}

export async function fetchMeeting(meetingId: string): Promise<Meeting | null> {
  const res = await fetch(`${BASE}/api/meetings/${meetingId}`, NO_CACHE);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`meeting fetch failed: ${res.status}`);
  return mapMeeting(await res.json());
}

// ev-accounts paginates the transcript at 200 segments/page
export async function fetchSegments(meetingId: string): Promise<Segment[]> {
  const all: Segment[] = [];
  for (let page = 1; ; page++) {
    const res = await fetch(
      `${BASE}/api/meetings/${meetingId}/transcript?page=${page}`,
      NO_CACHE
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
