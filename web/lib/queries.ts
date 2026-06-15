import type { Appearance, Meeting, MeetingSpeaker, MeetingSummary, Person, PersonDetail, Segment, TopicDetail, TopicListEntry } from "./types";

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
    summary_preview: m.summaryPreview ?? null,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    speakers: ((m.speakers ?? []) as any[]).map((sp): MeetingSpeaker => ({
      label: sp.label,
      display_name: sp.displayName ?? null,
      politician_slug: sp.politicianSlug ?? null,
      id_method: sp.idMethod ?? null,
      confidence: sp.confidence ?? null,
    })),
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapSummary(s: any): MeetingSummary {
  return {
    executive_summary: s.executiveSummary ?? "",
    key_decisions: s.keyDecisions ?? [],
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

export async function fetchPeople(): Promise<Person[]> {
  const res = await fetch(`${BASE}/api/people`);
  if (!res.ok) throw new Error(`people fetch failed: ${res.status}`);
  const data = await res.json();
  return (data as unknown[]).map(mapPerson);
}

export async function fetchPerson(slug: string): Promise<PersonDetail | null> {
  const res = await fetch(`${BASE}/api/people/${encodeURIComponent(slug)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`person fetch failed: ${res.status}`);
  const p = await res.json();
  return { ...mapPerson(p), bio_text: p.bioText ?? null };
}

export async function fetchAppearances(slug: string): Promise<Appearance[]> {
  const res = await fetch(
    `${BASE}/api/people/${encodeURIComponent(slug)}/appearances`
  );
  if (!res.ok) throw new Error(`appearances fetch failed: ${res.status}`);
  const { appearances } = (await res.json()) as { appearances: unknown[] };
  return appearances.map(mapAppearance);
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

export async function fetchSummary(meetingId: string): Promise<MeetingSummary | null> {
  const res = await fetch(`${BASE}/api/meetings/${encodeURIComponent(meetingId)}/summary`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`summary fetch failed: ${res.status}`);
  return mapSummary(await res.json());
}

export async function fetchTopics(): Promise<TopicListEntry[]> {
  const res = await fetch(`${BASE}/api/topics`);
  if (!res.ok) throw new Error(`topics fetch failed: ${res.status}`);
  const data = await res.json();
  return ((data.topics ?? []) as unknown[]).map(mapTopicEntry);
}

export async function fetchTopic(key: string): Promise<TopicDetail | null> {
  const res = await fetch(`${BASE}/api/topics/${encodeURIComponent(key)}`);
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
