import type { MetadataRoute } from "next";
import { fetchMeetings, fetchPeople, fetchTopics } from "@/lib/queries";

export const dynamic = "force-static";

const SITE = (process.env.SITE_URL ?? "").replace(/\/$/, "");

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  if (!SITE) return [];

  const staticRoutes: MetadataRoute.Sitemap = [
    { url: `${SITE}/`, changeFrequency: "daily", priority: 1 },
    { url: `${SITE}/people`, changeFrequency: "weekly", priority: 0.8 },
    { url: `${SITE}/search`, changeFrequency: "monthly", priority: 0.6 },
    { url: `${SITE}/topics`, changeFrequency: "weekly", priority: 0.7 },
  ];

  const [meetings, people, topics] = await Promise.all([
    fetchMeetings().catch(() => []),
    fetchPeople().catch(() => []),
    fetchTopics().catch(() => []),
  ]);

  const meetingRoutes: MetadataRoute.Sitemap = meetings.map((m) => ({
    url: `${SITE}/meetings/${m.meeting_id}`,
    lastModified: m.meeting_date,
    changeFrequency: "monthly",
    priority: 0.9,
  }));

  const peopleRoutes: MetadataRoute.Sitemap = people.map((p) => ({
    url: `${SITE}/people/${p.slug}`,
    changeFrequency: "monthly",
    priority: 0.7,
  }));

  const topicRoutes: MetadataRoute.Sitemap = topics.map((t) => ({
    url: `${SITE}/topics/${t.topic_key}`,
    changeFrequency: "weekly",
    priority: 0.6,
  }));

  return [...staticRoutes, ...meetingRoutes, ...peopleRoutes, ...topicRoutes];
}
