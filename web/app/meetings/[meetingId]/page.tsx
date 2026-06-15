import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchMeeting, fetchMeetings, fetchSegments, fetchSummary } from "@/lib/queries";
import { eventKindLabel, formatMeetingDate, meetingTitle } from "@/lib/format";
import MeetingView from "./MeetingView";

export const dynamicParams = false;

export async function generateStaticParams() {
  // Wrap in try/catch so builds succeed when EV_ACCOUNTS_URL is unset or the
  // API is unreachable (fetch throws TypeError: Invalid URL for relative paths).
  let meetings: Awaited<ReturnType<typeof fetchMeetings>> = [];
  try {
    meetings = await fetchMeetings();
  } catch {
    // API unavailable at build time — fall through to sentinel below.
  }
  // output:"export" fails the build when a dynamic route has zero params
  // (e.g., before the first meeting is published). Emit one sentinel id
  // that renders 404 so empty-data builds still succeed. Must be a valid
  // UUID — the API 422s on malformed ids but 404s on unknown ones.
  if (meetings.length === 0)
    return [{ meetingId: "00000000-0000-0000-0000-000000000000" }];
  return meetings.map((m) => ({ meetingId: m.meeting_id }));
}

const SUBSTANTIVE = new Set(["discussion", "public_comment", "consent_agenda", "vote"]);

export default async function MeetingPage({
  params,
}: {
  params: Promise<{ meetingId: string }>;
}) {
  const { meetingId } = await params;
  const meeting = await fetchMeeting(meetingId);
  if (!meeting) notFound();
  const [segments, summary] = await Promise.all([
    fetchSegments(meetingId),
    fetchSummary(meetingId).catch(() => null),
  ]);

  const outline = (summary?.sections ?? []).filter((s) => SUBSTANTIVE.has(s.section_type));

  return (
    <main className="meetingPage">
      <header className="meetingHeader">
        <Link href="/" className="backLink">
          ← All meetings
        </Link>
        <h1>{meetingTitle(meeting)}</h1>
        <span className="eventKind">{eventKindLabel(meeting.event_kind)}</span>
        <p className="meetingDate">{formatMeetingDate(meeting.meeting_date)}</p>
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

      {summary?.executive_summary && (
        <section className="execSummary">
          <h2>Summary</h2>
          <p>{summary.executive_summary}</p>
          {summary.key_decisions.length > 0 && (
            <ul className="keyDecisions">
              {summary.key_decisions.map((d, i) => <li key={i}>{d}</li>)}
            </ul>
          )}
        </section>
      )}

      <MeetingView meeting={meeting} segments={segments} outline={outline} />
    </main>
  );
}
