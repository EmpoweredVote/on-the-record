import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchMeeting, fetchMeetings, fetchSegments } from "@/lib/queries";
import MeetingView from "./MeetingView";

export const dynamicParams = false;

export async function generateStaticParams() {
  const meetings = await fetchMeetings();
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
