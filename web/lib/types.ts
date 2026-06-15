export interface Meeting {
  meeting_id: string;       // UUID from ev-accounts
  slug: string | null;      // human-readable slug (e.g. "2026-02-18-regular-session")
  city: string;
  body_slug: string | null;
  meeting_type: string;
  meeting_date: string;     // YYYY-MM-DD
  source_url: string | null;
  playback_kind: "youtube" | "file" | "hls" | null;
  playback_url: string | null;  // video_url from ev-accounts (resolved: YT id, file URL, etc.)
  duration_seconds: number | null;
  summary_preview: string | null;
  speakers: MeetingSpeaker[];
}

export interface Segment {
  meeting_id: string;           // UUID
  segment_id: number;           // segmentIndex from ev-accounts
  start_time: number;
  end_time: number;
  speaker_label: string;
  speaker_name: string | null;
  politician_slug: string | null;
  text: string;
}

export interface Person {
  slug: string;
  politician_id: string | null;   // essentials.politicians UUID
  name: string;
  headshot_url: string | null;
  party: string | null;
  office_title: string | null;
  district: string | null;
  jurisdiction: string | null;
  meeting_count: number;
  cities: string[];
  last_spoke_date: string | null; // YYYY-MM-DD
}

export interface PersonDetail extends Person {
  bio_text: string | null;
}

export interface AppearanceSegment {
  segment_id: number;             // segmentIndex from ev-accounts
  start_time: number;
  end_time: number;
  text: string;
}

export interface Appearance {
  meeting_id: string;
  city: string;
  meeting_type: string;
  meeting_date: string;           // YYYY-MM-DD
  playback_kind: string | null;
  segments: AppearanceSegment[];
}

export interface SearchResult {
  meeting_id: string;
  city: string;
  meeting_type: string;
  meeting_date: string;          // YYYY-MM-DD
  segment_id: number;            // segmentIndex from ev-accounts
  start_time: number;
  end_time: number;
  speaker_name: string | null;
  politician_slug: string | null;
  snippet: string;               // [[[match]]] sentinels, rendered as <mark>
}

export type ProvenanceStatus = "predicted" | "verified";

export interface SectionTopicRef {
  key: string;
  title: string | null;
  status: ProvenanceStatus;
}

export interface SummarySection {
  section_type: string;
  title: string;
  content: string;
  start_time: number | null;
  end_time: number | null;
  sort_order: number;
  topics: SectionTopicRef[];
}

export interface MeetingSummary {
  executive_summary: string;
  key_decisions: string[];
  model: string | null;
  sections: SummarySection[];
}

export interface MeetingSpeaker {
  label: string;
  display_name: string | null;
  politician_slug: string | null;
  id_method: string | null;   // "human_review" => verified; else predicted
  confidence: number | null;
}

export interface TopicListEntry {
  topic_key: string;
  title: string | null;
  item_count: number;
  meeting_count: number;
}

export interface TopicItem {
  meeting_id: string;
  city: string;
  meeting_type: string;
  meeting_date: string;
  playback_kind: string | null;
  section_index: number;       // for keying; deep links use start_time
  section_title: string | null;
  section_type: string | null;
  start_time: number | null;
  status: ProvenanceStatus;
}

export interface TopicDetail {
  topic_key: string;
  title: string | null;
  items: TopicItem[];
}
