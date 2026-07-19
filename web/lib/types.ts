export type EventKind =
  | "council"
  | "school_board"
  | "debate"
  | "forum"
  | "community_meeting"
  | "news_clip"
  | "press_conference"
  | "other";

export interface Meeting {
  meeting_id: string;       // UUID from ev-accounts
  slug: string | null;      // human-readable slug (e.g. "2026-02-18-regular-session")
  title: string | null;
  event_kind: EventKind;
  city: string | null;
  chamber_id: string | null;
  race_id: string | null;
  meeting_type: string;
  meeting_date: string;     // YYYY-MM-DD
  source_url: string | null;
  playback_kind: "youtube" | "file" | "hls" | null;
  playback_url: string | null;  // video_url from ev-accounts (resolved: YT id, file URL, etc.)
  duration_seconds: number | null;
  clip_start_seconds: number | null;
  clip_end_seconds: number | null;
  summary_preview: string | null;
  speakers: MeetingSpeaker[];
  speaker_count: number | null;  // count from the list API (speakers[] is detail-only)
  event_orgs: string[];         // hosting/producing organizations; may be empty
  source_title: string | null;  // title from yt-dlp metadata; used as title fallback
  thumbnail_url: string | null;  // extracted-frame thumbnail (Supabase Storage)
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
  politician_id: string;          // essentials.politicians UUID (the key + URL)
  name: string;
  headshot_url: string | null;
  // Party affiliation is intentionally not modeled — the site is anti-partisan.
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
  title: string | null;
  event_kind: EventKind;
  event_orgs: string[];
  source_title: string | null;
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
  politician_id: string | null;
  snippet: string;               // [[[match]]] sentinels, rendered as <mark>
  title: string | null;
  event_kind: EventKind;
  event_orgs: string[];
  source_title: string | null;
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
  highlights: string[];
  key_decisions?: string[];  // legacy field; accepted from API but not rendered
  model: string | null;
  sections: SummarySection[];
}

export interface MeetingSpeaker {
  label: string;
  display_name: string | null;
  politician_slug: string | null;
  politician_id: string | null;
  id_method: string | null;   // "human_review" => verified; else predicted
  confidence: number | null;
  local_slug: string | null;
  local_name: string | null;
  local_role: string | null;
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

export interface Vote {
  id: string;
  resolution: string | null;
  description: string | null;
  result: string;
  voteType: string | null;
  timestamp: number | null;
}

// A grabbed quote candidate, held client-side (localStorage) per politician.
// The tool stays read-only against ev-accounts; candidates never touch the
// compass taxonomy — `label` is a free-text personal tag reconciled to a real
// topic_key later, at publish. `orig_text` is the verbatim grab; `edit_text` is
// the trimmed/editorial version. `source_url` + `playback_kind` + `start_time`
// build the deep-link back to the moment in the source video.
export interface Candidate {
  id: string;                 // local id (crypto.randomUUID)
  politician_id: string;
  meeting_id: string;
  meeting_title: string;
  meeting_date: string;       // YYYY-MM-DD
  segment_id: number;         // originating segment (start segment for a cross-turn grab)
  start_time: number;         // seconds — segment start (deep-link target)
  source_url: string | null;  // meeting source (base url)
  playback_kind: string | null;
  orig_text: string;          // verbatim grab (never mutated)
  edit_text: string;          // editorial-trimmed version
  label: string;              // free-text topic label ("" = unlabeled)
  note: string;               // editor rationale: why selected + what was edited & why (required before publish export)
  starred: boolean;           // the one live pick for its label
  created_at: number;         // epoch ms
}
