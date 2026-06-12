import { supabase } from "./supabase";
import type { Meeting, Segment } from "./types";

export async function fetchMeetings(): Promise<Meeting[]> {
  const { data, error } = await supabase()
    .from("meetings")
    .select(
      "meeting_id, city, body_slug, meeting_type, meeting_date, source_url, playback_kind, playback_url, duration_seconds"
    )
    .order("meeting_date", { ascending: false });
  if (error) throw new Error(`meetings query failed: ${error.message}`);
  return data ?? [];
}

export async function fetchMeeting(meetingId: string): Promise<Meeting | null> {
  const { data, error } = await supabase()
    .from("meetings")
    .select(
      "meeting_id, city, body_slug, meeting_type, meeting_date, source_url, playback_kind, playback_url, duration_seconds"
    )
    .eq("meeting_id", meetingId)
    .maybeSingle();
  if (error) throw new Error(`meeting query failed: ${error.message}`);
  return data;
}

// Supabase caps responses at 1000 rows; a 3-hour meeting can exceed that,
// so page through with .range() until a short page signals the end.
const PAGE = 1000;

export async function fetchSegments(meetingId: string): Promise<Segment[]> {
  const all: Segment[] = [];
  for (let from = 0; ; from += PAGE) {
    const { data, error } = await supabase()
      .from("segments")
      .select(
        "meeting_id, segment_id, start_time, end_time, speaker_label, speaker_name, politician_slug, text"
      )
      .eq("meeting_id", meetingId)
      .order("segment_id", { ascending: true })
      .range(from, from + PAGE - 1);
    if (error) throw new Error(`segments query failed: ${error.message}`);
    all.push(...(data ?? []));
    if (!data || data.length < PAGE) break;
  }
  return all;
}
