import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchMeeting, fetchMeetings, fetchSegments } from "@/lib/queries";
import MeetingView from "./MeetingView";

export const dynamicParams = false;

export async function generateStaticParams() {
  const meetings = await fetchMeetings();
  // output:"export" fails the build when a dynamic route has zero params
  // (e.g., before the first meeting is published). Emit one sentinel id
  // that renders 404 so empty-data builds still succeed. Must be a valid
  // UUID — the API 422s on malformed ids but 404s on unknown ones.
  if (meetings.length === 0)
    return [{ meetingId: "00000000-0000-0000-0000-000000000000" }];
  return meetings.map((m) => ({ meetingId: m.meeting_id }));
}

export default async function MeetingPage({
  params,
}: {
  params: Promise<{ meetingId: string }>;
}) {
  const { meetingId } = await params;
  const meeting = await fetchMeeting(meetingId);
  if (!meeting) notFound();
  const segments = await fetchSegments(meetingId);

  return (
    <main className="meetingPage">
      <header className="meetingHeader">
        <Link href="/" className="backLink">
          ← All meetings
        </Link>
        <h1>
          {meeting.city} {meeting.meeting_type} — {meeting.meeting_date}
        </h1>
        {meeting.source_url && (
          <a
            className="sourceLink"
            href={meeting.source_url}
            target="_blank"
            rel="noreferrer"
          >
            Original source ↗
          </a>
        )}
      </header>
      <MeetingView meeting={meeting} segments={segments} />
    </main>
  );
}
