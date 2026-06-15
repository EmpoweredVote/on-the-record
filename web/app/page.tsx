import Link from "next/link";
import { fetchMeetings } from "@/lib/queries";
import { formatMeetingDate } from "@/lib/format";

function formatDuration(seconds: number | null): string {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export default async function HomePage() {
  let meetings: Awaited<ReturnType<typeof fetchMeetings>> = [];
  let loadError = false;
  try {
    meetings = await fetchMeetings();
  } catch {
    // Don't take the whole site down (or fail CI builds) on a DB hiccup.
    loadError = true;
  }

  return (
    <main className="indexPage">
      <h1>Meetings</h1>
      <p className="tagline">
        Searchable, speaker-attributed transcripts of public meetings, synced
        to the original video.
      </p>
      <nav className="siteNav">
        <Link href="/people">People →</Link>
        <Link href="/search">Search →</Link>
        <Link href="/topics">Topics →</Link>
      </nav>
      {loadError ? (
        <p>Meetings are temporarily unavailable. Please try again shortly.</p>
      ) : meetings.length === 0 ? (
        <p>No meetings published yet.</p>
      ) : (
        <ul className="meetingList">
          {meetings.map((m) => (
            <li key={m.meeting_id}>
              <Link href={`/meetings/${m.meeting_id}`}>
                <span className="meetingTitle">
                  {m.city} {m.meeting_type}
                </span>
                <span className="meetingDate">{formatMeetingDate(m.meeting_date)}</span>
                {m.duration_seconds ? (
                  <span className="meetingDuration">
                    {formatDuration(m.duration_seconds)}
                  </span>
                ) : null}
                {m.playback_kind ? (
                  <span className="hasVideo">▶ video</span>
                ) : null}
                {m.summary_preview && <span className="meetingPreview">{m.summary_preview}</span>}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
