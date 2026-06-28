"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { fetchMeeting, fetchSegments, fetchSummary } from "@/lib/queries";
import { eventKindLabel, formatMeetingDate, meetingTitle } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import MeetingView from "./MeetingView";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import NotFound from "@/components/NotFound";

const SUBSTANTIVE = new Set(["discussion", "public_comment", "consent_agenda", "vote"]);

export default function MeetingDetailClient() {
  const params = useParams<{ meetingId: string }>();
  const id = params.meetingId;

  const meetingQ = useApi(() => fetchMeeting(id), [id]);
  const segmentsQ = useApi(() => fetchSegments(id), [id]);
  const summaryQ = useApi(() => fetchSummary(id).catch(() => null), [id]);

  if (meetingQ.loading) return <main className="meetingPage"><Loading label="Loading meeting…" /></main>;
  if (meetingQ.error) return <main className="meetingPage"><ErrorState /></main>;
  if (!meetingQ.data) return <NotFound message="Meeting not found." />;

  const meeting = meetingQ.data;
  const segments = segmentsQ.data ?? [];
  const summary = summaryQ.data ?? null;
  const outline = (summary?.sections ?? []).filter((s) => SUBSTANTIVE.has(s.section_type));

  return (
    <main className="meetingPage">
      <header className="meetingHeader">
        <Link href="/" className="backLink">← All meetings</Link>
        <h1>{meetingTitle(meeting)}</h1>
        <span className="eventKind">{eventKindLabel(meeting.event_kind)}</span>
        {meeting.event_orgs.length > 0 && (
          <p className="eventOrgs">{meeting.event_orgs.join(" · ")}</p>
        )}
        <p className="meetingDate">{formatMeetingDate(meeting.meeting_date)}</p>
        {meeting.source_url && (
          <a className="sourceLink" href={meeting.source_url} target="_blank" rel="noreferrer">
            Original source ↗
          </a>
        )}
      </header>

      {summary?.executive_summary && (
        <section className="execSummary">
          <h2>Summary</h2>
          <p>{summary.executive_summary}</p>
          {summary.highlights.length > 0 && (
            <ul className="highlights">
              {summary.highlights.map((d, i) => <li key={i}>{d}</li>)}
            </ul>
          )}
        </section>
      )}

      <MeetingView meeting={meeting} segments={segments} outline={outline} />
    </main>
  );
}
