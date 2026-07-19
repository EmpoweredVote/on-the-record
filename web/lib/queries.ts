import type {
  Appearance,
  EventKind,
  Meeting,
  MeetingSpeaker,
  MeetingSummary,
  Person,
  PersonDetail,
  Segment,
  TopicDetail,
  TopicListEntry,
  Vote,
} from "./types";

// Read at call time (not import time) so it's testable and still inlined by Next.
function base(): string {
  return (process.env.NEXT_PUBLIC_EV_ACCOUNTS_URL ?? "").replace(/\/$/, "");
}

// Always fetch current data in the browser; no build-time cache.
const FETCH_INIT: RequestInit = { cache: "no-store" };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapMeeting(m: any): Meeting {
  return {
    meeting_id: m.id,
    slug: m.slug ?? null,
    title: m.title ?? null,
    event_kind: (m.eventKind ?? "council") as EventKind,
    city: m.city ?? null,
    chamber_id: m.chamberId ?? null,
    race_id: m.raceId ?? null,
    meeting_type: m.meetingType,
    meeting_date: m.date,
    source_url: m.sourceUrl ?? null,
    playback_kind: m.playbackKind ?? null,
    playback_url: m.videoUrl ?? null,
    duration_seconds: m.durationSeconds ?? null,
    clip_start_seconds: m.clipStartSeconds ?? null,
    clip_end_seconds: m.clipEndSeconds ?? null,
    summary_preview: m.summaryPreview ?? null,
    event_orgs: (m.eventOrgs ?? []) as string[],
    source_title: m.processingMetadata?.sourceTitle ?? null,
    thumbnail_url: m.thumbnailUrl ?? null,
    speaker_count: m.speakerCount ?? null,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    speakers: ((m.speakers ?? []) as any[]).map((sp): MeetingSpeaker => ({
      label: sp.label,
      display_name: sp.displayName ?? null,
      politician_slug: sp.politicianSlug ?? null,
      politician_id: sp.politicianId ?? null,
      id_method: sp.idMethod ?? null,
      confidence: sp.confidence ?? null,
      local_slug: sp.localSlug ?? null,
      local_name: sp.localName ?? null,
      local_role: sp.localRole ?? null,
    })),
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapSummary(s: any): MeetingSummary {
  return {
    executive_summary: s.executiveSummary ?? "",
    highlights: s.highlights ?? s.keyDecisions ?? [],
    model: s.model ?? null,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    sections: ((s.sections ?? []) as any[]).map((sec) => ({
      section_type: sec.sectionType,
      title: sec.title,
      content: sec.content,
      start_time: sec.startTime ?? null,
      end_time: sec.endTime ?? null,
      sort_order: sec.sortOrder ?? 0,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      topics: ((sec.topics ?? []) as any[]).map((t) => ({
        key: t.key, title: t.title ?? null, status: (t.status ?? "predicted"),
      })),
    })),
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapTopicEntry(t: any): TopicListEntry {
  return {
    topic_key: t.topicKey, title: t.title ?? null,
    item_count: t.itemCount ?? 0, meeting_count: t.meetingCount ?? 0,
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
    politician_id: p.politicianId,
    name: p.name,
    headshot_url: p.headshotUrl ?? null,
    // Party affiliation from the API is intentionally dropped — anti-partisan.
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
    title: a.title ?? null,
    event_kind: (a.eventKind ?? "council") as EventKind,
    event_orgs: (a.eventOrgs ?? []) as string[],
    source_title: a.sourceTitle ?? null,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    segments: ((a.segments ?? []) as any[]).map((s) => ({
      segment_id: s.segmentIndex,
      start_time: s.startTime,
      end_time: s.endTime,
      text: s.text,
    })),
  };
}

export async function fetchPeople(): Promise<Person[]> {
  if (!base()) return [];
  const res = await fetch(`${base()}/api/people`, FETCH_INIT);
  if (!res.ok) throw new Error(`people fetch failed: ${res.status}`);
  const data = await res.json();
  return (data as unknown[]).map(mapPerson);
}

export async function fetchPerson(id: string): Promise<PersonDetail | null> {
  if (!base()) return null;
  const res = await fetch(`${base()}/api/people/${encodeURIComponent(id)}`, FETCH_INIT);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`person fetch failed: ${res.status}`);
  const p = await res.json();
  return { ...mapPerson(p), bio_text: p.bioText ?? null };
}

export async function fetchAppearances(id: string): Promise<Appearance[]> {
  if (!base()) return [];
  const res = await fetch(
    `${base()}/api/people/${encodeURIComponent(id)}/appearances`,
    FETCH_INIT
  );
  if (!res.ok) throw new Error(`appearances fetch failed: ${res.status}`);
  const { appearances } = (await res.json()) as { appearances: unknown[] };
  return appearances.map(mapAppearance);
}

export async function fetchMeetings(): Promise<Meeting[]> {
  if (!base()) return [];
  const res = await fetch(`${base()}/api/meetings`, FETCH_INIT);
  if (!res.ok) throw new Error(`meetings fetch failed: ${res.status}`);
  const data = await res.json();
  return (data as unknown[]).map(mapMeeting);
}

export async function fetchMeeting(meetingId: string): Promise<Meeting | null> {
  if (!base()) return null;
  const res = await fetch(`${base()}/api/meetings/${meetingId}`, FETCH_INIT);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`meeting fetch failed: ${res.status}`);
  return mapMeeting(await res.json());
}

// ev-accounts paginates the transcript at 200 segments/page
export async function fetchSegments(meetingId: string): Promise<Segment[]> {
  if (!base()) return [];
  const all: Segment[] = [];
  for (let page = 1; ; page++) {
    const res = await fetch(
      `${base()}/api/meetings/${meetingId}/transcript?page=${page}`,
      FETCH_INIT
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

// Meeting roll-call votes. Empty for meetings without a published vote record;
// unmatched votes carry a null timestamp (not click-to-seekable).
export async function fetchVotes(meetingId: string): Promise<Vote[]> {
  const res = await fetch(`${base()}/api/meetings/${meetingId}/votes`, FETCH_INIT);
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`votes fetch failed: ${res.status}`);
  const raw = (await res.json()) as any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  return raw.map((v) => ({
    id: v.id,
    resolution: v.resolution ?? null,
    description: v.description ?? null,
    result: v.result ?? "",
    voteType: v.voteType ?? null,
    timestamp: v.timestamp ?? null,
  }));
}

export async function fetchSummary(meetingId: string): Promise<MeetingSummary | null> {
  if (!base()) return null;
  const res = await fetch(`${base()}/api/meetings/${encodeURIComponent(meetingId)}/summary`, FETCH_INIT);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`summary fetch failed: ${res.status}`);
  return mapSummary(await res.json());
}

export async function fetchTopics(): Promise<TopicListEntry[]> {
  if (!base()) return [];
  const res = await fetch(`${base()}/api/topics`, FETCH_INIT);
  if (!res.ok) throw new Error(`topics fetch failed: ${res.status}`);
  const data = await res.json();
  return ((data.topics ?? []) as unknown[]).map(mapTopicEntry);
}

export async function fetchTopic(key: string): Promise<TopicDetail | null> {
  if (!base()) return null;
  const res = await fetch(`${base()}/api/topics/${encodeURIComponent(key)}`, FETCH_INIT);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`topic fetch failed: ${res.status}`);
  const t = await res.json();
  return {
    topic_key: t.topicKey,
    title: t.title ?? null,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    items: ((t.items ?? []) as any[]).map((it) => ({
      meeting_id: it.meetingId, city: it.city, meeting_type: it.meetingType,
      meeting_date: it.date, playback_kind: it.playbackKind ?? null,
      section_index: it.sectionIndex, section_title: it.sectionTitle ?? null,
      section_type: it.sectionType ?? null, start_time: it.startTime ?? null,
      status: (it.status ?? "predicted"),
    })),
  };
}
