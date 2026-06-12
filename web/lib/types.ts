export interface Meeting {
  meeting_id: string;
  city: string;
  body_slug: string | null;
  meeting_type: string;
  meeting_date: string;
  source_url: string | null;
  playback_kind: "youtube" | "file" | "hls" | null;
  playback_url: string | null;
  duration_seconds: number | null;
}

export interface Segment {
  meeting_id: string;
  segment_id: number;
  start_time: number;
  end_time: number;
  speaker_label: string;
  speaker_name: string | null;
  politician_slug: string | null;
  text: string;
}
