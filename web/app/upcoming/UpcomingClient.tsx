"use client";

import Link from "next/link";
import { fetchAgendaItems, fetchUpcomingMeetings } from "@/lib/queries";
import { formatMeetingWhen, groupByDate } from "@/lib/upcoming";
import { useApi } from "@/lib/useApi";
import type { Meeting } from "@/lib/types";
import Breadcrumbs from "@/components/Breadcrumbs";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import EmptyState from "@/components/EmptyState";

// Only kinds the adapter interprets into plain language get listed on the
// index; procedural items (minutes approval, adjournment, ...) stay on the
// meeting's own agenda page.
const INTERPRETED_KINDS = new Set([
  "ordinance",
  "resolution",
  "appointment",
  "public-comment",
]);

// Agenda items load per meeting, after the meeting list. A meeting whose
// agenda hasn't been interpreted yet (or whose fetch fails) simply shows no
// item list — the meeting card itself is still useful.
function UpcomingAgenda({ meetingId }: { meetingId: string }) {
  const { data: items } = useApi(
    () => fetchAgendaItems(meetingId).catch(() => []),
    [meetingId]
  );
  const shown = (items ?? []).filter((it) => INTERPRETED_KINDS.has(it.kind));
  if (shown.length === 0) return null;

  return (
    <ul className="upcomingItems">
      {shown.map((item) => (
        <li key={item.id}>
          <Link href={`/items/${item.id}`} className="upcomingItemLink">
            <span className="upcomingItemTitle">
              {item.summary_plain ?? item.title_raw}
            </span>
            {item.stage && (
              <span className="upcomingItemStage">{item.stage}</span>
            )}
            {item.public_comment && (
              <span className="upcomingItemComment">Public comment</span>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
}

function UpcomingMeeting({ meeting }: { meeting: Meeting }) {
  return (
    <article className="upcomingMeeting">
      <h2>{meeting.title ?? meeting.meeting_type}</h2>
      <p className="upcomingWhen">
        {formatMeetingWhen(meeting.meeting_date, meeting.starts_at, meeting.timezone)}
      </p>
      {meeting.city && <p className="upcomingWhere">{meeting.city}</p>}
      <UpcomingAgenda meetingId={meeting.meeting_id} />
      {meeting.source_url && (
        <a
          className="upcomingSource"
          href={meeting.source_url}
          target="_blank"
          rel="noreferrer"
        >
          Official agenda (PDF)
        </a>
      )}
    </article>
  );
}

export default function UpcomingClient() {
  const { data: meetings, loading, error } = useApi(fetchUpcomingMeetings);

  return (
    <main className="indexPage">
      <Breadcrumbs items={[{ label: "Meetings", href: "/" }, { label: "Upcoming" }]} />
      <h1>Upcoming meetings</h1>
      <p className="tagline">
        What&apos;s on the agenda at upcoming public meetings, in plain language.
      </p>
      {loading ? (
        <Loading label="Loading upcoming meetings…" />
      ) : error ? (
        <ErrorState message="Upcoming meetings are temporarily unavailable. Please try again shortly." />
      ) : !meetings || meetings.length === 0 ? (
        <EmptyState message="No upcoming meetings scheduled yet." />
      ) : (
        groupByDate(meetings).map((group) => (
          <section key={group.date} className="upcomingGroup">
            {group.meetings.map((m) => (
              <UpcomingMeeting key={m.meeting_id} meeting={m} />
            ))}
          </section>
        ))
      )}
    </main>
  );
}
