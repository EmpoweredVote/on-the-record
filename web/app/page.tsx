import Link from "next/link";
import { fetchMeetings } from "@/lib/queries";
import MeetingListClient from "./MeetingListClient";

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
        <MeetingListClient meetings={meetings} />
      )}
    </main>
  );
}
