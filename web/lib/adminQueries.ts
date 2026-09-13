// Authenticated fetchers for the /admin review panel. Kept separate from
// queries.ts so the public app stays unauthenticated and published-only.
import { authedFetch } from "./adminAuth";
import { mapMeeting, mapSummary, mapSegment } from "./queries";
import type { Meeting, Segment, MeetingSummary, Vote } from "./types";

export type DraftListItem = Meeting & { named: number; linked: number };

export async function fetchDraftMeetings(status = "draft"): Promise<DraftListItem[]> {
  const res = await authedFetch(`/api/admin/meetings?status=${encodeURIComponent(status)}`);
  if (!res.ok) return [];
  const raw = (await res.json()) as any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  // Admin list rows carry the raw `id` alongside the mapped `meeting_id`, since
  // the draft queue keys/links by the ev-accounts id the API returns.
  return raw.map((r) => ({
    ...mapMeeting(r),
    id: r.id,
    named: Number(r.named ?? 0),
    linked: Number(r.linked ?? 0),
  }));
}

export async function fetchAdminMeeting(id: string): Promise<Meeting | null> {
  const res = await authedFetch(`/api/admin/meetings/${id}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`admin meeting fetch failed: ${res.status}`);
  return mapMeeting(await res.json());
}

export async function fetchAdminSegments(id: string): Promise<Segment[]> {
  const all: Segment[] = [];
  for (let page = 1; ; page++) {
    const res = await authedFetch(`/api/admin/meetings/${id}/transcript?page=${page}`);
    if (!res.ok) throw new Error(`admin transcript fetch failed: ${res.status}`);
    const { segments, totalCount } = (await res.json()) as {
      segments: unknown[]; page: number; totalCount: number;
    };
    all.push(...segments.map(mapSegment));
    if (all.length >= totalCount) break;
  }
  return all;
}

export async function fetchAdminSummary(id: string): Promise<MeetingSummary | null> {
  const res = await authedFetch(`/api/admin/meetings/${id}/summary`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`admin summary fetch failed: ${res.status}`);
  return mapSummary(await res.json());
}

export async function fetchAdminVotes(id: string): Promise<Vote[]> {
  const res = await authedFetch(`/api/admin/meetings/${id}/votes`);
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`admin votes fetch failed: ${res.status}`);
  const raw = (await res.json()) as any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  return raw.map((v) => ({
    id: v.id,
    resolution: v.resolution ?? null,
    description: v.description ?? null,
    result: v.result ?? "",
    voteType: v.voteType ?? null,
    timestamp: v.timestamp ?? null,
  }));
}

async function patchStatus(id: string, status: string): Promise<void> {
  const res = await authedFetch(`/api/admin/meetings/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!res.ok) throw new Error(`status change to ${status} failed: ${res.status}`);
}

export async function promoteMeeting(id: string): Promise<void> { await patchStatus(id, "published"); }
export async function archiveMeeting(id: string): Promise<void> { await patchStatus(id, "archived"); }
