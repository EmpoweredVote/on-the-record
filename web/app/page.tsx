"use client";

import Link from "next/link";
import { fetchMeetings } from "@/lib/queries";
import { useApi } from "@/lib/useApi";
import MeetingListClient from "./MeetingListClient";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import EmptyState from "@/components/EmptyState";

export default function HomePage() {
  const { data: meetings, loading, error } = useApi(fetchMeetings);

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
      {loading ? (
        <Loading label="Loading meetings…" />
      ) : error ? (
        <ErrorState message="Meetings are temporarily unavailable. Please try again shortly." />
      ) : !meetings || meetings.length === 0 ? (
        <EmptyState message="No meetings published yet." />
      ) : (
        <MeetingListClient meetings={meetings} />
      )}
    </main>
  );
}
