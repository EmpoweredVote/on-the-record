import Link from "next/link";
import type { Meeting } from "@/lib/types";
import { eventKindLabel, formatMeetingDate, meetingTitle } from "@/lib/format";
import MeetingThumbnail from "./MeetingThumbnail";

export default function MeetingCard({ meeting }: { meeting: Meeting }) {
  const speakerCount = meeting.speaker_count ?? 0;
  const date = formatMeetingDate(meeting.meeting_date);
  // speaker_count comes from the list API; render only when present —
  // never show "0 speakers".
  const meta =
    speakerCount > 0
      ? `${date} · ${speakerCount} ${speakerCount === 1 ? "speaker" : "speakers"}`
      : date;

  return (
    <li>
      <Link href={`/meetings/${meeting.meeting_id}`} className="meetingCard">
        <MeetingThumbnail meeting={meeting} />
        <div className="meetingBody">
          <span className="eventKind">{eventKindLabel(meeting.event_kind)}</span>
          <span className="meetingTitle">{meetingTitle(meeting)}</span>
          <span className="meetingMeta">{meta}</span>
          {meeting.summary_preview && (
            <span className="meetingPreview">{meeting.summary_preview}</span>
          )}
        </div>
      </Link>
    </li>
  );
}
