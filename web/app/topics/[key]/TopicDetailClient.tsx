"use client";

import Link from "next/link";
import { fetchTopic } from "@/lib/queries";
import { formatMeetingDate } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { usePathParam } from "@/lib/usePathParam";
import ProvenanceBadge from "@/components/ProvenanceBadge";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import NotFound from "@/components/NotFound";

function fmt(seconds: number | null): string {
  if (seconds == null) return "";
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.floor(seconds % 60);
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export default function TopicDetailClient() {
  const key = usePathParam(1); // /topics/<key> — real URL key, not the build sentinel
  const ready = key != null;

  const topicQ = useApi(() => (ready ? fetchTopic(key) : Promise.resolve(null)), [key]);

  if (!ready || topicQ.loading) return <main className="indexPage"><Loading label="Loading topic…" /></main>;
  if (topicQ.error) return <main className="indexPage"><ErrorState /></main>;
  if (!topicQ.data) return <NotFound message="Topic not found." />;

  const topic = topicQ.data;

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
              {it.city} {it.meeting_type} · {formatMeetingDate(it.meeting_date)} <ProvenanceBadge status={it.status} />
            </span>
          </li>
        ))}
      </ul>
    </main>
  );
}
