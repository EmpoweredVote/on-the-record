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
