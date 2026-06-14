import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchTopic, fetchTopics } from "@/lib/queries";
import ProvenanceBadge from "@/components/ProvenanceBadge";

export const dynamicParams = false;

export async function generateStaticParams() {
  let topics: Awaited<ReturnType<typeof fetchTopics>> = [];
  try { topics = await fetchTopics(); } catch { /* empty-DB build */ }
  if (topics.length === 0) return [{ key: "none" }];
  return topics.map((t) => ({ key: t.topic_key }));
}

function fmt(seconds: number | null): string {
  if (seconds == null) return "";
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.floor(seconds % 60);
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export default async function TopicPage({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const topic = await fetchTopic(key);
  if (!topic) notFound();

  return (
    <main className="indexPage">
      <Link href="/topics" className="backLink">← All topics</Link>
      <h1>{topic.title ?? topic.topic_key}</h1>
      <p className="tagline">{topic.items.length} item{topic.items.length === 1 ? "" : "s"} across meetings</p>
      <ul className="topicItems">
        {topic.items.map((it) => (
          <li key={`${it.meeting_id}:${it.section_index}`} className="topicItem">
            <Link href={`/meetings/${it.meeting_id}?t=${it.start_time != null ? Math.floor(it.start_time) : 0}`} className="topicItemLink">
              <span className="topicItemTitle">{it.section_title ?? "Discussion"}</span>
              <span className="topicItemTime">{fmt(it.start_time)}</span>
            </Link>
            <span className="topicItemMeta">
              {it.city} {it.meeting_type} · {it.meeting_date} <ProvenanceBadge status={it.status} />
            </span>
          </li>
        ))}
      </ul>
    </main>
  );
}
