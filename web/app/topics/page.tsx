import Link from "next/link";
import { fetchTopics } from "@/lib/queries";
import type { TopicListEntry } from "@/lib/types";

export const metadata = { title: "Topics — On the Record" };

export default async function TopicsPage() {
  let topics: TopicListEntry[] = [];
  let loadError = false;
  try { topics = await fetchTopics(); } catch { loadError = true; }

  return (
    <main className="indexPage">
      <Link href="/" className="backLink">← All meetings</Link>
      <h1>Topics</h1>
      <p className="tagline">Issues discussed across meetings, from the Compass topic set.</p>
      {loadError ? (
        <p>Topics are temporarily unavailable.</p>
      ) : topics.length === 0 ? (
        <p>No topics tagged yet.</p>
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
