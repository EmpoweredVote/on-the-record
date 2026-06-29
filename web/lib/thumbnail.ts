import type { Meeting } from "./types";
import { formatDuration, formatMeetingDate, meetingTitle } from "./format";

export interface ThumbnailModel {
  /** Real video frame to show; null => render the info tile instead. */
  imageSrc: string | null;
  /** Whether a playable video exists (controls the centered play overlay). */
  showPlay: boolean;
  /** Formatted duration for the badge, or null to hide it. */
  duration: string | null;
  /** True when the meeting has no video at all. */
  transcriptOnly: boolean;
  /** Info-tile location line (city, falling back to the meeting title). */
  location: string;
  /** Info-tile date line. */
  date: string;
}

/** Public YouTube thumbnail URL for a video id. */
export function youtubeThumbnailUrl(videoId: string): string {
  return `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;
}

export function buildThumbnailModel(meeting: Meeting): ThumbnailModel {
  const hasVideo = meeting.playback_kind !== null && meeting.playback_url !== null;

  // Source precedence: explicit extracted thumbnail > YouTube-derived frame > none.
  let imageSrc: string | null = null;
  if (meeting.thumbnail_url) {
    imageSrc = meeting.thumbnail_url;
  } else if (meeting.playback_kind === "youtube" && meeting.playback_url) {
    imageSrc = youtubeThumbnailUrl(meeting.playback_url);
  }

  const duration =
    hasVideo && meeting.duration_seconds
      ? formatDuration(meeting.duration_seconds)
      : null;

  return {
    imageSrc,
    showPlay: hasVideo,
    duration,
    transcriptOnly: !hasVideo,
    location: meeting.city?.trim() || meetingTitle(meeting),
    date: formatMeetingDate(meeting.meeting_date),
  };
}
