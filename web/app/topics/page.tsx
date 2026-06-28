"use client";

import Link from "next/link";
import { fetchTopics } from "@/lib/queries";
import { useApi } from "@/lib/useApi";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import EmptyState from "@/components/EmptyState";

export default function TopicsPage() {
  const { data: topics, loading, error } = useApi(fetchTopics);

  return (
    <main className="indexPage">
      <Link href="/" className="backLink">← All meetings</Link>
      <h1>Topics</h1>
      <p className="tagline">Issues discussed across meetings, from the Compass topic set.</p>
      {loading ? (
        <Loading label="Loading topics…" />
      ) : error ? (
        <ErrorState message="Topics are temporarily unavailable." />
      ) : !topics || topics.length === 0 ? (
        <EmptyState message="No topics yet." />
      ) : (
        <ul className="topicList">
          {topics.map((t) => (
            <li key={t.topic_key}>
              <Link href={`/topics/${t.topic_key}`} className="topicRow">
                <span className="topicName">{t.title ?? t.topic_key}</span>
                <span className="topicCount">
                  {t.item_count} item{t.item_count === 1 ? "" : "s"} · {t.meeting_count} meeting{t.meeting_count === 1 ? "" : "s"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
